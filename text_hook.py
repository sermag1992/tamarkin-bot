"""Генерация цепляющего хука для поста через Pollinations text API (бесплатно)."""
import json
import logging
import re
from typing import Dict, Optional

import aiohttp

from image_generator import retry_async

logger = logging.getLogger(__name__)

TEXT_API_URL = "https://text.pollinations.ai/openai"

PROMPT_TEMPLATE = (
    "Ты редактор Telegram-канала о маркетинге для предпринимателей. "
    "Напиши пост по новости ниже. "
    'Ответ строго в формате JSON: {{"hook": "...", "body": "..."}} — только JSON, без markdown. '
    "hook — цепляющая первая строка до 90 символов: интригует, задевает боль или любопытство, "
    "но без кликбейта, клише и эмодзи, на русском. "
    "body — суть новости 2-3 короткими предложениями, простым языком, без выдумывания фактов. "
    "Новость. Заголовок: {title}. Текст: {summary}"
)


def _strip_fences(text: str) -> str:
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text)
    text = re.sub(r"\s*```$", "", text)
    return text.strip()


def _parse_hook(text: str) -> Optional[Dict[str, str]]:
    """Разбор ответа модели. Возвращает {'hook': ..., 'body': ...} или None."""
    try:
        data = json.loads(_strip_fences(text))
    except (json.JSONDecodeError, ValueError):
        return None
    hook, body = data.get("hook"), data.get("body")
    if not isinstance(hook, str) or not isinstance(body, str):
        return None
    hook, body = hook.strip(), body.strip()
    if not hook or not body:
        return None
    return {
        "hook": hook[:90].strip(),
        "body": body[:600].strip(),
    }


class RelevanceChecker:
    """Семантический фильтр: LLM оценивает, подходит ли новость каналу о маркетинге.
    Батч-режим: все новости оцениваются одним запросом (лимит бесплатного API —
    1 запрос в 45 секунд на IP, поэтому перебор по одной недопустим)."""

    PROMPT_HEADER = (
        "Ты редактор Telegram-канала о маркетинге и продажах для предпринимателей.\n"
        "Отбери новости, которые стоит показать аудитории канала.\n"
        "Подходят: маркетинг, реклама, продажи, воронки, трафик, SEO, контент, "
        "личный бренд, нейросети и AI в маркетинге и бизнесе, полезные бизнесу тренды.\n"
        "Не подходят: чисто технические темы (программирование, базы данных, железо, "
        "девопс), криптовалюты и биржи, политика, происшествия, спорт, культура.\n"
    )
    PROMPT_FOOTER = (
        'Ответ строго JSON: {"verdicts": [{"i": 1, "fit": true, "reason": "кратко по-русски"}, '
        "...]} — вердикт по каждой новости в исходном порядке, только JSON."
    )

    def __init__(self, model: str = "openai", temperature: float = 0.0) -> None:
        self.model = model
        self.temperature = temperature

    def build_prompt(self, items: list) -> str:
        lines = [self.PROMPT_HEADER, "Новости (нумерация важна):"]
        for idx, it in enumerate(items, start=1):
            title = getattr(it, "title", str(it))
            summary = getattr(it, "summary", "") or title
            if len(summary) > 200:
                summary = summary[:197] + "…"
            lines.append(f"[{idx}] Заголовок: {title} | Текст: {summary}")
        lines.append(self.PROMPT_FOOTER)
        return "\n".join(lines)

    @retry_async(retries=2, base_delay=50.0)
    async def check_batch(
        self, session: aiohttp.ClientSession, items: list
    ) -> Dict[int, Dict]:
        """Оценивает список новостей одним запросом.
        Возвращает {порядковый номер: {'fit': bool, 'reason': str}}."""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": self.build_prompt(items)}],
            "temperature": self.temperature,
        }
        async with session.post(
            TEXT_API_URL, json=payload, timeout=aiohttp.ClientTimeout(total=120)
        ) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(f"Text API вернул {response.status}: {text[:200]}")
            data = await response.json()

        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        try:
            parsed = json.loads(_strip_fences(content))
            verdicts = {}
            for v in parsed.get("verdicts", []):
                i = v.get("i")
                if isinstance(i, int) and 1 <= i <= len(items):
                    verdicts[i] = {
                        "fit": bool(v.get("fit")),
                        "reason": str(v.get("reason", ""))[:200],
                    }
            if not verdicts:
                raise ValueError(f"пустой список вердиктов: {content[:200]}")
            return verdicts
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise RuntimeError(f"Не удалось разобрать оценку: {content[:200]}") from exc


class HookWriter:
    """Переписывает заголовок и суть новости в цепляющий пост. Фолбэк — исходный текст."""

    def __init__(self, model: str = "openai", temperature: float = 0.7) -> None:
        self.model = model
        self.temperature = temperature

    def build_prompt(self, title: str, summary: str) -> str:
        return PROMPT_TEMPLATE.format(title=title, summary=summary)

    @retry_async(retries=2, base_delay=50.0)  # лимит free-тарифа: 1 запрос в 45 сек
    async def write(
        self, session: aiohttp.ClientSession, title: str, summary: str
    ) -> Optional[Dict[str, str]]:
        """Возвращает {'hook', 'body'} или бросает исключение после всех попыток."""
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": self.build_prompt(title, summary)}],
            "temperature": self.temperature,
        }
        async with session.post(
            TEXT_API_URL, json=payload, timeout=aiohttp.ClientTimeout(total=120)
        ) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(f"Text API вернул {response.status}: {text[:200]}")
            data = await response.json()

        content = (data.get("choices") or [{}])[0].get("message", {}).get("content", "")
        parsed = _parse_hook(content)
        if parsed is None:
            raise RuntimeError(f"Не удалось разобрать ответ модели: {content[:200]}")
        return parsed
