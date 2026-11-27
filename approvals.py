"""Личное одобрение новостей: кандидаты приходят владельцу в Telegram,
публикация в канал — по кнопке или команде «публикуй»."""
import html
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
from aiogram import Bot
from aiogram.types import (
    BufferedInputFile,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from config import Config
from image_generator import ImageGenerator
from news_parser import NewsItem, PostedStore
from telegram_poster import TelegramPoster
from text_hook import HookWriter

logger = logging.getLogger(__name__)

PUBLISH_PREFIXES = ("публику", "опублику", "пост", "publish")
SKIP_PREFIXES = ("пропусти", "скип", "skip", "нет")

HELP_TEXT = (
    "Бот на связи ✅\n\n"
    "Буду присылать сюда свежие новости для канала. "
    "Под каждой — кнопки:\n"
    "✅ Опубликовать — пост уйдёт в канал\n"
    "⏭ Пропустить — уберу из очереди\n\n"
    "Можно и словами: «публикуй» (все из очереди), "
    "«публикуй 2» (конкретная), «пропустить 1»."
)


def load_json_file(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def save_json_file(path: str, data: Any) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def parse_command(text: str) -> Tuple[Optional[str], Optional[int]]:
    """Разбор команды владельца. Возвращает (действие, номер|None)."""
    t = (text or "").strip().lower()
    if not t:
        return None, None
    if t.startswith("/start"):
        return "start", None
    words = re.split(r"[\s,!?]+", t)
    first = words[0] if words else ""
    digits = re.search(r"\d+", " ".join(words[1:]))
    num = int(digits.group()) if digits else None
    if any(first.startswith(p) for p in PUBLISH_PREFIXES):
        return "publish", num
    if any(first.startswith(p) for p in SKIP_PREFIXES):
        return "skip", num
    return None, None


class PendingStore:
    """Новости, отправленные владельцу и ожидающие решения."""

    def __init__(self, path: str) -> None:
        self.path = path
        self.items: List[dict] = load_json_file(path, [])

    def save(self) -> None:
        save_json_file(self.path, self.items)

    def hashes(self) -> set:
        return {i["hash"] for i in self.items}

    def add(self, item: dict) -> None:
        self.items.append(item)

    def remove(self, id8: str) -> None:
        self.items = [i for i in self.items if i["id8"] != id8]

    def find(self, id8: str) -> Optional[dict]:
        return next((i for i in self.items if i["id8"] == id8), None)


def candidate_caption(title: str, summary: str, link: str, num: int, suffix: str = "") -> str:
    """Подпись к карточке кандидата в личке владельца."""
    summary = summary or title
    if len(summary) > 280:
        summary = summary[:277] + "…"
    return (
        f"<b>Новость №{num}</b>\n\n"
        f"<b>{html.escape(title)}</b>\n\n"
        f"{html.escape(summary)}\n\n"
        f"{link}"
        f"{suffix}"
    )


def _keyboard(id8: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="✅ Опубликовать", callback_data=f"pub:{id8}"),
        InlineKeyboardButton(text="⏭ Пропустить", callback_data=f"skip:{id8}"),
    ]])


def _empty_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[])


async def _edit_card(
    bot: Bot, item: dict, suffix: str
) -> None:
    """Пометить карточку кандидата решением и убрать кнопки."""
    caption = candidate_caption(
        item["title"], item["summary"], item["link"], item["num"], suffix
    )
    try:
        if item.get("kind") == "photo":
            await bot.edit_message_caption(
                chat_id=item["chat_id"],
                message_id=item["msg_id"],
                caption=caption,
                parse_mode="HTML",
                reply_markup=_empty_keyboard(),
            )
        else:
            await bot.edit_message_text(
                chat_id=item["chat_id"],
                message_id=item["msg_id"],
                text=caption,
                parse_mode="HTML",
                reply_markup=_empty_keyboard(),
            )
    except Exception as exc:
        logger.warning("Не удалось обновить карточку кандидата: %s", exc)


async def publish_pending_item(
    session: aiohttp.ClientSession,
    config: Config,
    item: dict,
    generator: ImageGenerator,
    poster: TelegramPoster,
) -> bool:
    """Публикация одобренной новости в канал: хук + картинка + пост."""
    hook = ""
    body = item.get("summary") or item["title"]
    try:
        hw = await HookWriter().write(session, item["title"], body)
        hook, body = hw["hook"], hw["body"]
        logger.info("Хук сгенерирован: %s", hook)
    except Exception as exc:
        logger.warning("Хук не сгенерирован (%s), публикую исходный текст.", exc)

    image_bytes = None
    if item.get("image_url"):
        image_bytes = await ImageGenerator.download_image(session, item["image_url"])
    if image_bytes is None:
        try:
            image_bytes = await generator.generate_pollinations(
                session, generator.build_prompt(item["title"], body)
            )
        except Exception as exc:
            logger.warning("Картинка не получена: %s", exc)

    if image_bytes:
        return await poster.post(item["title"], body, item["link"], image_bytes, hook=hook)
    return await poster.post_text(item["title"], body, item["link"], hook=hook)


async def _handle_callback(
    bot: Bot,
    cb,
    config: Config,
    session: aiohttp.ClientSession,
    pending: PendingStore,
    posted: PostedStore,
    generator: ImageGenerator,
    poster: TelegramPoster,
    owner: Optional[dict],
) -> None:
    if not owner or (cb.from_user and cb.from_user.id != owner.get("id")):
        return
    action, _, id8 = (cb.data or "").partition(":")
    item = pending.find(id8) if id8 else None
    if item is None:
        await bot.answer_callback_query(callback_query_id=cb.id, text="Уже решено ранее")
        return

    if action == "pub":
        ok = await publish_pending_item(session, config, item, generator, poster)
        if ok:
            posted.mark_posted(item["hash"])
            posted.save()
            pending.remove(id8)
            pending.save()
            await _edit_card(bot, item, "\n\n✅ <b>Опубликовано в канал</b>")
            await bot.answer_callback_query(callback_query_id=cb.id, text="Готово — пост в канале")
            logger.info("Опубликовано по кнопке: %s", item["title"])
        else:
            await bot.answer_callback_query(
                callback_query_id=cb.id, text="Не получилось, попробуй ещё раз"
            )
    elif action == "skip":
        pending.remove(id8)
        pending.save()
        await _edit_card(bot, item, "\n\n⏭ <b>Пропущено</b>")
        await bot.answer_callback_query(callback_query_id=cb.id, text="Пропустил")


async def _handle_message(
    bot: Bot,
    msg,
    config: Config,
    session: aiohttp.ClientSession,
    pending: PendingStore,
    posted: PostedStore,
    generator: ImageGenerator,
    poster: TelegramPoster,
    owner: Optional[dict],
    owner_path: str,
) -> None:
    user_id = msg.from_user.id if msg.from_user else None
    if user_id is None:
        return
    action, num = parse_command(msg.text or "")
    chat_id = msg.chat.id

    if action == "start":
        if owner is None:
            save_json_file(
                owner_path,
                {"id": user_id, "first_name": msg.from_user.first_name},
            )
            logger.info("Зарегистрирован владелец: id=%s", user_id)
        await bot.send_message(chat_id=chat_id, text=HELP_TEXT)
        return

    if owner is None or user_id != owner.get("id"):
        return

    if action == "publish":
        targets = (
            list(pending.items)
            if num is None
            else [pending.items[num - 1]]
            if 1 <= num <= len(pending.items)
            else []
        )
        if not targets:
            await bot.send_message(
                chat_id=chat_id, text="Нет новостей в ожидании решения."
            )
            return
        ok_count = 0
        for item in list(targets):
            if await publish_pending_item(session, config, item, generator, poster):
                posted.mark_posted(item["hash"])
                posted.save()
                pending.remove(item["id8"])
                pending.save()
                await _edit_card(bot, item, "\n\n✅ <b>Опубликовано в канал</b>")
                ok_count += 1
        fail = len(targets) - ok_count
        reply = f"✅ Опубликовано: {ok_count}"
        if fail:
            reply += f", не удалось: {fail} (остались в очереди)"
        await bot.send_message(chat_id=chat_id, text=reply)

    elif action == "skip":
        targets = (
            list(pending.items)
            if num is None
            else [pending.items[num - 1]]
            if 1 <= num <= len(pending.items)
            else []
        )
        for item in targets:
            pending.remove(item["id8"])
            pending.save()
            await _edit_card(bot, item, "\n\n⏭ <b>Пропущено</b>")
        await bot.send_message(chat_id=chat_id, text=f"⏭ Пропущено: {len(targets)}")
    else:
        await bot.send_message(
            chat_id=chat_id,
            text="Не понял команду. Жми кнопки под новостью или пиши "
            "«публикуй» / «публикуй 2» / «пропустить 1».",
        )


async def process_updates(
    bot: Bot,
    session: aiohttp.ClientSession,
    config: Config,
    pending: PendingStore,
    posted: PostedStore,
    generator: ImageGenerator,
    poster: TelegramPoster,
    owner_path: str,
) -> None:
    """Забирает накопившиеся действия владельца (кнопки, команды) и выполняет их."""
    state_dir = os.path.dirname(config.PENDING_JSON_PATH)
    offset_path = os.path.join(state_dir, "updates_offset.json")
    offset = int(load_json_file(offset_path, {"offset": 0})["offset"])
    owner = load_json_file(owner_path, None)

    try:
        updates = await bot.get_updates(offset=offset, timeout=0)
    except Exception as exc:
        logger.error("Не удалось получить обновления Telegram: %s", exc)
        return
    if not updates:
        return

    for upd in updates:
        offset = max(offset, upd.update_id + 1)
        try:
            if upd.callback_query:
                await _handle_callback(
                    bot, upd.callback_query, config, session,
                    pending, posted, generator, poster, owner,
                )
            elif upd.message and upd.message.text:
                await _handle_message(
                    bot, upd.message, config, session,
                    pending, posted, generator, poster, owner, owner_path,
                )
        except Exception as exc:
            logger.error("Ошибка обработки действия владельца: %s", exc)

    save_json_file(offset_path, {"offset": offset})


async def expire_pending(bot: Bot, pending: PendingStore, config: Config) -> None:
    """Убирает из очереди кандидатов, по которым владелец так и не решил."""
    now = datetime.now(timezone.utc)
    keep: List[dict] = []
    expired = 0
    for item in pending.items:
        try:
            sent_at = datetime.fromisoformat(item["sent_at"])
            if now - sent_at > timedelta(hours=config.PENDING_TTL_HOURS):
                await _edit_card(bot, item, "\n\n⌫ <i>Устарело, удалено из очереди</i>")
                expired += 1
                continue
        except Exception:
            pass
        keep.append(item)
    if expired:
        pending.items = keep
        pending.save()
        logger.info("Из очереди убрано устаревших кандидатов: %d", expired)


async def send_candidates(
    bot: Bot,
    session: aiohttp.ClientSession,
    config: Config,
    pending: PendingStore,
    batch: List[NewsItem],
    owner: dict,
) -> int:
    """Рассылает новые кандидаты владельцу в личку с кнопками решения."""
    sent = 0
    existing_nums = [i.get("num", 0) for i in pending.items]
    next_num = max(existing_nums, default=0)
    for item in batch:
        next_num += 1
        num = next_num
        entry = {
            "id8": item.news_hash[:8],
            "hash": item.news_hash,
            "title": item.title,
            "link": item.link,
            "summary": item.summary,
            "image_url": item.image_url,
            "chat_id": owner["id"],
            "num": num,
            "kind": "text",
            "msg_id": 0,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        }
        caption = candidate_caption(item.title, item.summary, item.link, num)
        photo_bytes = None
        if item.image_url:
            photo_bytes = await ImageGenerator.download_image(session, item.image_url)
        try:
            if photo_bytes:
                m = await bot.send_photo(
                    chat_id=owner["id"],
                    photo=BufferedInputFile(photo_bytes, "news.jpg"),
                    caption=caption,
                    parse_mode="HTML",
                    reply_markup=_keyboard(entry["id8"]),
                )
                entry["kind"] = "photo"
            else:
                m = await bot.send_message(
                    chat_id=owner["id"],
                    text=caption,
                    parse_mode="HTML",
                    reply_markup=_keyboard(entry["id8"]),
                )
            entry["msg_id"] = m.message_id
            pending.add(entry)
            pending.save()
            sent += 1
            logger.info("Кандидат отправлен владельцу (№%d): %s", num, item.title)
        except Exception as exc:
            logger.error("Не удалось отправить кандидата: %s", exc)
    return sent
