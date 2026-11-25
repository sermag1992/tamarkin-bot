import html
import logging

from aiogram import Bot
from aiogram.types import BufferedInputFile

from image_generator import retry_async

logger = logging.getLogger(__name__)


class TelegramPoster:
    def __init__(self, bot: Bot, channel_id: str) -> None:
        self.bot = bot
        self.channel_id = channel_id

    @staticmethod
    def build_caption(title: str, summary: str, link: str) -> str:
        """HTML-подпись к посту: жирный заголовок, суть, ссылка на источник."""
        title = title if len(title) <= 100 else title[:97] + "..."
        summary = summary if len(summary) <= 600 else summary[:597] + "..."
        esc_title = html.escape(title)
        esc_summary = html.escape(summary)
        esc_link = html.escape(link, quote=True)
        return (
            f"<b>{esc_title}</b>\n\n"
            f"{esc_summary}\n\n"
            f'Источник: <a href="{esc_link}">Читать оригинал</a>'
        )

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

    async def post(
        self, title: str, summary: str, link: str, image_bytes: bytes
    ) -> bool:
        """Публикация поста с картинкой. Возвращает True при успехе."""
        caption = self.build_caption(title, summary, link)
        photo = BufferedInputFile(file=image_bytes, filename="news.jpg")
        try:
            await self._send_photo(photo, caption)
            logger.info("Пост опубликован в канал %s: %s", self.channel_id, title)
            return True
        except Exception as exc:
            logger.error("Ошибка отправки поста в Telegram: %s", exc)
            return False

    async def post_text(self, title: str, summary: str, link: str) -> bool:
        """Резервная публикация без картинки, если изображение получить не удалось."""
        try:
            await self._send_message(self.build_caption(title, summary, link))
            logger.info(
                "Текстовый пост опубликован в канал %s: %s", self.channel_id, title
            )
            return True
        except Exception as exc:
            logger.error("Ошибка отправки текстового поста в Telegram: %s", exc)
            return False
