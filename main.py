import asyncio
import logging
import os
import sys

import aiohttp

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer

from approvals import (
    PendingStore,
    expire_pending,
    load_json_file,
    process_updates,
    send_candidates,
)
from config import BASE_DIR, Config
from image_generator import ImageGenerator
from news_parser import fetch_news, PostedStore
from telegram_poster import TelegramPoster
from text_hook import RelevanceChecker

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


async def run_once(session: aiohttp.ClientSession, config: Config) -> None:
    """Один цикл: выполнить решения владельца → прислать ему новые кандидаты.
    В канал публикуется только то, что владелец одобрил в личке."""
    store = PostedStore(config.POSTED_JSON_PATH)
    pending = PendingStore(config.PENDING_JSON_PATH)
    state_dir = os.path.dirname(config.PENDING_JSON_PATH)
    owner_path = os.path.join(state_dir, "owner.json")

    api_server = TelegramAPIServer(
        base=f"{config.TELEGRAM_API_BASE}/bot{{token}}/{{method}}",
        file=f"{config.TELEGRAM_API_BASE}/file/bot{{token}}/{{path}}",
    )
    bot = Bot(token=config.TELEGRAM_BOT_TOKEN, session=AiohttpSession(api=api_server))
    generator = ImageGenerator(
        api_key=config.OPENAI_API_KEY,
        model=config.OPENAI_IMAGE_MODEL,
        size=config.OPENAI_IMAGE_SIZE,
    )
    poster = TelegramPoster(bot=bot, channel_id=config.TELEGRAM_CHANNEL_ID)

    try:
        # 1) сначала выполняем накопившиеся решения владельца (кнопки, команды)
        await process_updates(
            bot, session, config, pending, store, generator, poster, owner_path
        )
        # 2) чистим очередь от устаревших кандидатов
        await expire_pending(bot, pending, config)

        # 3) шлём новые кандидаты, если есть куда и есть место в очереди
        owner = load_json_file(owner_path, None)
        if not owner:
            logger.info(
                "Владелец ещё не привязан. Напиши боту /start в Telegram — "
                "после этого новости будут приходить в личку на одобрение."
            )
            return

        free = config.MAX_PENDING - len(pending.items)
        if free <= 0:
            logger.info(
                "В очереди уже %d кандидатов — жду решения по ним.", len(pending.items)
            )
            return

        news = await fetch_news(
            session,
            config.RSS_URLS,
            store,
            keywords=config.KEYWORDS,
            stopwords=config.STOPWORDS,
            max_age_hours=config.MAX_NEWS_AGE_HOURS,
        )
        fresh = [i for i in news if i.news_hash not in pending.hashes()]
        # Семантический фильтр: LLM одним запросом отсекает нерелевантное
        # ДО отправки владельцу
        if config.LLM_FILTER_ENABLED and fresh:
            to_check = fresh[: config.CANDIDATES_PER_RUN * 3]
            try:
                verdicts = await RelevanceChecker().check_batch(session, to_check)
                relevant = []
                for idx, i in enumerate(to_check, start=1):
                    verdict = verdicts.get(idx)
                    if verdict is None:
                        logger.warning("Фильтр не дал вердикта, пропускаю дальше: %s", i.title)
                        relevant.append(i)
                    elif verdict["fit"]:
                        relevant.append(i)
                    else:
                        logger.info(
                            "Отфильтровано как нерелевантное: %s — %s",
                            i.title, verdict["reason"],
                        )
                fresh = relevant
            except Exception as exc:
                # фильтр сломался — не теряем новости, владелец всё равно решает сам
                logger.warning("Фильтр недоступен (%s), отправляю без фильтрации.", exc)

        batch = fresh[: min(config.CANDIDATES_PER_RUN, free)]
        if not batch:
            logger.info("Новых релевантных новостей для одобрения нет.")
            return

        sent = await send_candidates(bot, session, config, pending, batch, owner)
        logger.info("Отправлено кандидатов владельцу: %d", sent)

    except Exception as exc:
        logger.error("Ошибка цикла: %s", exc)
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
        async with aiohttp.ClientSession(timeout=timeout) as session:
            await run_once(session, config)
        return

    interval_minutes = max(config.CHECK_INTERVAL_MINUTES, 1)
    logger.info(
        "Бот запущен. Проверка каждые %d мин. Канал: %s",
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
