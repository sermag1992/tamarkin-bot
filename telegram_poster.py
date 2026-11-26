import html
import logging

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import BufferedInputFile

from image_generator import retry_async

logger = logging.getLogger(__name__)


class TelegramPoster:
    def __init__(self, bot: Bot, channel_id: str) -> None:
        self.bot = bot
        self.channel_id = channel_id

    @staticmethod
    def build_caption(
        title: str, summary: str, link: str, hook: str = ""
    ) -> str:
        """HTML-подпись к посту: хук-крючок, жирный заголовок, суть, ссылка.
        Укладывается в лимит Telegram для подписи к фото (1024 символа)."""
        title = title if len(title) <= 100 else title[:97] + "..."
        esc_title = html.escape(title)
        esc_link = html.escape(link, quote=True)

        hook_part = f"<i>{html.escape(hook.strip()[:90])}</i>\n\n" if hook else ""
        header = f"<b>{esc_title}</b>\n\n"
        footer = f"\n\nИсточник: <a href=\"{esc_link}\">Читать оригинал</a>"

        budget = 1024 - len(hook_part) - len(header) - len(footer)
        body = html.escape(summary)
        if len(body) > budget:
            cut = body[: budget - 1].rsplit(" ", 1)[0].rstrip(" ,;:-–—")
            body = cut + "…"

        return hook_part + header + body + footer

    @retry_async(retries=3, base_delay=1.0)
    async def _send_photo(self, photo: BufferedInputFile, caption: str) -> None:
        await self.bot.send_photo(
            chat_id=self.channel_id,
            photo=photo,
            caption=caption,
            parse_mode="HTML",
        )

    @retry_async(retries=3, base_delay=1.0)
    async def _send_message(self, text: str) -> None:
        await self.bot.send_message(
            chat_id=self.channel_id,
            text=text,
            parse_mode="HTML",
        )

    async def _send_with_topic_fix(
        self, send_call, error_tag: str
    ) -> bool:
        """Отправка с авто-лечением TOPIC_CLOSED: открываем «Общее» и повторяем."""
        try:
            await send_call()
            return True
        except TelegramBadRequest as exc:
            if "TOPIC_CLOSED" not in str(exc):
                logger.error("%s: ошибка Telegram: %s", error_tag, exc)
                return False
            logger.warning("Общая тема канала закрыта — открываю и повторяю...")
            try:
                await self.bot.reopen_general_forum_topic(chat_id=self.channel_id)
            except Exception as reopen_exc:
                logger.error(
                    "%s: не удалось открыть общую тему (нужны права админа). "
                    "Открой её вручную: канал → Обсуждения → «Общее» → Открыть тему. "
                    "Причина: %s", error_tag, reopen_exc
                )
                return False
            try:
                await send_call()
                logger.info("Пост прошёл после открытия общей темы.")
                return True
            except Exception as retry_exc:
                logger.error("%s: повторная отправка не удалась: %s", error_tag, retry_exc)
                return False

    async def post(
        self, title: str, summary: str, link: str, image_bytes: bytes, hook: str = ""
    ) -> bool:
        """Публикация поста с картинкой. Возвращает True при успехе."""
        caption = self.build_caption(title, summary, link, hook)
        photo = BufferedInputFile(file=image_bytes, filename="news.jpg")
        try:
            ok = await self._send_with_topic_fix(
                lambda: self._send_photo(photo, caption), "Пост с картинкой"
            )
            if ok:
                logger.info("Пост опубликован в канал %s: %s", self.channel_id, title)
            return ok
        except Exception as exc:
            logger.error("Ошибка отправки поста в Telegram: %s", exc)
            return False

    async def post_text(self, title: str, summary: str, link: str, hook: str = "") -> bool:
        """Резервная публикация без картинки, если изображение получить не удалось."""
        try:
            ok = await self._send_with_topic_fix(
                lambda: self._send_message(
                    self.build_caption(title, summary, link, hook)
                ),
                "Текстовый пост",
            )
            if ok:
                logger.info(
                    "Текстовый пост опубликован в канал %s: %s", self.channel_id, title
                )
            return ok
        except Exception as exc:
            logger.error("Ошибка отправки текстового поста в Telegram: %s", exc)
            return False
