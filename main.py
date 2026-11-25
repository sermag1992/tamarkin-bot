import asyncio
import html
import logging
import os
import sys
from typing import Optional

import aiohttp

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession

from config import BASE_DIR, Config
from image_generator import ImageGenerator
from news_parser import fetch_news, PostedStore
from telegram_poster import TelegramPoster

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("main")


def setup_file_logging() -> None:
    """Дублирует логи в bot.log рядом с main.py — чтобы бот можно было запустить без консоли."""
    handler = logging.FileHandler(
        os.path.join(BASE_DIR, "bot.log"), encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    )
    logging.getLogger().addHandler(handler)


async def run_once(session: aiohttp.ClientSession, config: Config) -> Optional[bool]:
    """Один цикл: взять одну новую новость → картинка → пост → сохранить хэш.
    Возвращает None, если кандидатов нет; True, если пост вышел; False, если ошибка."""
    store = PostedStore(config.POSTED_JSON_PATH)
    tg_session = AiohttpSession(api=config.TELEGRAM_API_BASE)
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN, session=tg_session)
    generator = ImageGenerator(
        api_key=config.OPENAI_API_KEY,
        model=config.OPENAI_IMAGE_MODEL,
        size=config.OPENAI_IMAGE_SIZE,
    )
    poster = TelegramPoster(bot=bot, channel_id=config.TELEGRAM_CHANNEL_ID)

    try:
        news_items = await fetch_news(
            session,
            config.RSS_URLS,
            store,
            keywords=config.KEYWORDS,
            stopwords=config.STOPWORDS,
            max_age_hours=config.MAX_NEWS_AGE_HOURS,
        )
        if not news_items:
            logger.info("Новых новостей для публикации не найдено.")
            return None

        item = news_items[0]  # не более одной новости за запуск
        logger.info("Выбрана новость: %s", item.title)

        image_bytes: Optional[bytes] = None

        if config.IMAGE_MODE == "rss" and item.image_url:
            logger.info("Загрузка изображения из RSS: %s", item.image_url)
            image_bytes = await generator.download_image(session, item.image_url)

        if (
            image_bytes is None
            and config.IMAGE_MODE == "openai"
            and config.OPENAI_API_KEY
        ):
            logger.info("Генерация изображения через OpenAI...")
            prompt = generator.build_prompt(item.title, item.summary)
            try:
                image_bytes = await generator.generate(session, prompt)
            except Exception as exc:
                logger.warning(
                    "OpenAI не справился (%s), пробуем Pollinations.ai...", exc
                )

        if image_bytes is None:
            logger.info("Генерация изображения через Pollinations.ai...")
            prompt = generator.build_prompt(item.title, item.summary)
            try:
                image_bytes = await generator.generate_pollinations(session, prompt)
            except Exception as exc:
                logger.error("Pollinations.ai не сгенерировал изображение: %s", exc)

        success = False
        if image_bytes:
            success = await poster.post(
                item.title, item.summary, item.link, image_bytes
            )
        else:
            logger.warning("Изображение недоступно, публикую пост без картинки.")
            success = await poster.post_text(item.title, item.summary, item.link)

        if success:
            store.mark_posted(item.news_hash)
            store.save()
            logger.info("Хэш сохранён, новость больше не будет повторяться: %s", item.title)
        else:
            logger.error("Пост не опубликован: %s", item.title)
        return success

    except Exception as exc:
        logger.error("Ошибка обработки новости: %s", exc)
        return False
    finally:
        await bot.session.close()


async def main() -> None:
    config = Config.from_env()
    logging.getLogger().setLevel(
        getattr(logging, config.LOG_LEVEL.upper(), logging.INFO)
    )
    timeout = aiohttp.ClientTimeout(total=180)

    if config.CHECK_INTERVAL_MINUTES <= 0:
        # Одноразовый режим: один проход и выход (для GitHub Actions / хостинга).
        # Код выхода 1, если новость была, но опубликовать не удалось.
        async with aiohttp.ClientSession(timeout=timeout) as session:
            posted = await run_once(session, config)
        if posted is False:
            sys.exit(1)
        return

    interval_minutes = max(config.CHECK_INTERVAL_MINUTES, 1)
    logger.info(
        "Бот запущен. Проверка новостей каждые %d мин. Канал: %s",
        interval_minutes,
        config.TELEGRAM_CHANNEL_ID,
    )
    async with aiohttp.ClientSession(timeout=timeout) as session:
        while True:
            await run_once(session, config)
            logger.info("Следующая проверка через %d минут.", interval_minutes)
            await asyncio.sleep(interval_minutes * 60)


if __name__ == "__main__":
    try:
        setup_file_logging()
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Бот остановлен пользователем.")
        sys.exit(0)
