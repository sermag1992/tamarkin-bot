import asyncio
import base64
import functools
import logging
import urllib.parse
from typing import Callable, Optional, TypeVar

import aiohttp

logger = logging.getLogger(__name__)

OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
POLLINATIONS_URL = "https://image.pollinations.ai/prompt/{}"
IMAGE_CONTENT_TYPES = ("image/jpeg", "image/png", "image/webp")
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

T = TypeVar("T")


def retry_async(retries: int = 3, base_delay: float = 1.0) -> Callable:
    """Декоратор с экспоненциальной задержкой для асинхронных функций."""

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        async def wrapper(*args, **kwargs) -> T:
            delay = base_delay
            last_exc: Optional[Exception] = None
            for attempt in range(1, retries + 1):
                try:
                    return await func(*args, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    logger.warning(
                        "Попытка %d/%d для %s не удалась: %s",
                        attempt,
                        retries,
                        func.__name__,
                        exc,
                    )
                    if attempt < retries:
                        await asyncio.sleep(delay)
                        delay *= 2
            assert last_exc is not None
            raise last_exc

        return wrapper

    return decorator


class ImageGenerator:
    def __init__(
        self, api_key: str, model: str = "gpt-image-1", size: str = "1024x1024"
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.size = size

    @retry_async(retries=3, base_delay=1.0)
    async def generate(
        self, session: aiohttp.ClientSession, prompt: str
    ) -> Optional[bytes]:
        """Генерация иллюстрации через OpenAI Images API (gpt-image-1). Возвращает байты картинки."""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "prompt": prompt,
            "n": 1,
            "size": self.size,
        }
        async with session.post(
            OPENAI_IMAGES_URL,
            json=payload,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=120),
        ) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(f"OpenAI API вернул {response.status}: {text}")
            data = await response.json()

        try:
            b64_json = data["data"][0]["b64_json"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError(f"Пустой ответ от OpenAI Images API: {data}") from exc

        return base64.b64decode(b64_json)

    @retry_async(retries=3, base_delay=50.0)
    async def generate_pollinations(
        self, session: aiohttp.ClientSession, prompt: str
    ) -> Optional[bytes]:
        """Бесплатная генерация через Pollinations.ai (лимит: 1 картинка в 45 сек)."""
        encoded = urllib.parse.quote(prompt)
        url = POLLINATIONS_URL.format(encoded) + "?width=1024&height=1024&nologo=true"
        async with session.get(
            url, timeout=aiohttp.ClientTimeout(total=120)
        ) as response:
            if response.status != 200:
                text = await response.text()
                raise RuntimeError(f"Pollinations вернул {response.status}: {text}")
            if response.content_type not in IMAGE_CONTENT_TYPES:
                raise RuntimeError(
                    f"Pollinations вернул не изображение: {response.content_type}"
                )
            return await response.read()

    @staticmethod
    async def download_image(
        session: aiohttp.ClientSession, url: str
    ) -> Optional[bytes]:
        """Скачивание изображения по URL (например, из RSS enclosure)."""
        async with session.get(
            url, headers=HEADERS, timeout=aiohttp.ClientTimeout(total=60)
        ) as response:
            if response.status != 200:
                logger.warning(
                    "Не удалось скачать изображение %s: %s", url, response.status
                )
                return None
            if response.content_type not in IMAGE_CONTENT_TYPES:
                logger.warning(
                    "По ссылке %s не изображение: %s", url, response.content_type
                )
                return None
            return await response.read()

    def build_prompt(self, title: str, summary: str) -> str:
        return (
            f"Editorial illustration for a marketing Telegram channel. "
            f"Topic: {title}. Summary: {summary}. "
            f"Clean modern flat vector style, no text, no logos, square composition."
        )
