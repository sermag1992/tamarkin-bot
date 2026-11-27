import hashlib
import html as html_mod
import json
import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
from typing import List, Optional

import aiohttp

from image_generator import retry_async

logger = logging.getLogger(__name__)

RSS_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}


@dataclass
class NewsItem:
    title: str
    link: str
    summary: str
    news_hash: str
    fetched_at: str
    image_url: Optional[str] = None
    published_at: Optional[str] = None


class PostedStore:
    def __init__(self, json_path: str = "posted.json") -> None:
        self.json_path = json_path
        self._posted: set = self._load()

    def _load(self) -> set:
        try:
            with open(self.json_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return set(data) if isinstance(data, list) else set()
        except (FileNotFoundError, json.JSONDecodeError):
            return set()

    def save(self) -> None:
        with open(self.json_path, "w", encoding="utf-8") as f:
            json.dump(sorted(self._posted), f, ensure_ascii=False, indent=2)

    def is_posted(self, news_hash: str) -> bool:
        return news_hash in self._posted

    def mark_posted(self, news_hash: str) -> None:
        self._posted.add(news_hash)


def _hash_title(title: str) -> str:
    return hashlib.sha256(title.encode("utf-8")).hexdigest()


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)  # &#039; → ' и т.п.
    return re.sub(r"\s+", " ", text).strip()


def _summarize(text: str, max_sentences: int = 3) -> str:
    text = _clean_html(text)
    if not text:
        return ""
    sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if s.strip()]
    return " ".join(sentences[:max_sentences])


def _truncate_title(title: str, limit: int = 100) -> str:
    """Обрезка заголовка до limit символов по границе слова."""
    if len(title) <= limit:
        return title
    cut = title[: limit - 1].rsplit(" ", 1)[0].rstrip(" ,;:-–—")
    return (cut + "…")[:limit]


def _looks_like_image(url: str) -> bool:
    return any(ext in url.lower() for ext in (".jpg", ".jpeg", ".png", ".webp"))


def _extract_image_url(item, description: str = "") -> Optional[str]:
    """Обложка статьи: media:content → enclosure → первая картинка в description."""
    ns = {"media": "http://search.yahoo.com/mrss/"}
    media = item.find("media:content", ns)
    if media is not None:
        url = media.get("url")
        if url:
            return url
    enclosure = item.find("enclosure")
    if enclosure is not None:
        url = enclosure.get("url")
        etype = (enclosure.get("type") or "").lower()
        if url and (etype.startswith("image/") or _looks_like_image(url) or not etype):
            return url
    img = re.search(r'<img[^>]+src="([^"]+)"', description or "")
    if img:
        return img.group(1)
    return None


def _parse_pub_date(item) -> Optional[str]:
    pub_date = item.findtext("pubDate")
    if not pub_date:
        return None
    try:
        dt = parsedate_to_datetime(pub_date)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
    except Exception:
        return None


def _is_recent(published_at: Optional[str], max_hours: int) -> bool:
    if not published_at:
        return True
    try:
        dt = datetime.fromisoformat(published_at)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        age = datetime.now(timezone.utc) - dt
        return age <= timedelta(hours=max_hours)
    except Exception:
        return True


def _matches_filter(title: str, summary: str, keywords: List[str], stopwords: List[str]) -> bool:
    """Пустой список keywords = фильтр по ключевым словам отключён (решает LLM)."""
    text = f"{title} {summary}".lower()
    if keywords and not any(kw.lower() in text for kw in keywords):
        return False
    if any(sw.lower() in text for sw in stopwords):
        return False
    return True


@retry_async(retries=3, base_delay=1.0)
async def _fetch_rss_text(session: aiohttp.ClientSession, rss_url: str) -> str:
    async with session.get(
        rss_url, headers=RSS_HEADERS, timeout=aiohttp.ClientTimeout(total=30)
    ) as response:
        response.raise_for_status()
        # response.text() сам учитывает charset из заголовка (например,
        # 4PDA отдаёт ленту в windows-1251)
        return await response.text()


async def fetch_news(
    session: aiohttp.ClientSession,
    rss_urls: str,
    store: PostedStore,
    keywords: List[str],
    stopwords: List[str],
    max_age_hours: int,
) -> List[NewsItem]:
    """Получение свежих непопубликованных новостей из одного или нескольких RSS."""
    urls = [u.strip() for u in rss_urls.split(",") if u.strip()]
    items: List[NewsItem] = []

    for url in urls:
        try:
            xml_text = await _fetch_rss_text(session, url)
        except Exception as exc:
            logger.error("Ошибка загрузки RSS %s: %s", url, exc)
            continue

        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError as exc:
            logger.error("Некорректный XML в RSS %s: %s", url, exc)
            continue

        for item in root.findall(".//item"):
            title = _clean_html(item.findtext("title") or "")
            link = item.findtext("link") or ""
            description = item.findtext("description") or ""
            if not title or not link:
                continue

            news_hash = _hash_title(title)
            if store.is_posted(news_hash):
                continue

            if not _matches_filter(title, description, keywords, stopwords):
                continue

            published_at = _parse_pub_date(item)
            if not _is_recent(published_at, max_age_hours):
                continue

            summary = _summarize(description) or title
            image_url = _extract_image_url(item, description)

            items.append(
                NewsItem(
                    title=_truncate_title(title),
                    link=link,
                    summary=summary,
                    news_hash=news_hash,
                    fetched_at=datetime.now(timezone.utc).isoformat(),
                    image_url=image_url,
                    published_at=published_at,
                )
            )

    items.sort(key=lambda x: x.published_at or "", reverse=True)
    return items
