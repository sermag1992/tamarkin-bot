import os
from dataclasses import dataclass
from typing import List

from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, ".env"))


@dataclass(frozen=True)
class Config:
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_API_BASE: str
    TELEGRAM_CHANNEL_ID: str
    RSS_URLS: str
    IMAGE_MODE: str
    POSTED_JSON_PATH: str
    LOG_LEVEL: str
    OPENAI_API_KEY: str
    OPENAI_IMAGE_MODEL: str
    OPENAI_IMAGE_SIZE: str
    KEYWORDS: List[str]
    STOPWORDS: List[str]
    MAX_NEWS_AGE_HOURS: int
    CHECK_INTERVAL_MINUTES: int

    @classmethod
    def from_env(cls) -> "Config":
        required = {
            "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN"),
            "TELEGRAM_CHANNEL_ID": os.getenv("TELEGRAM_CHANNEL_ID"),
            "RSS_URLS": os.getenv("RSS_URLS"),
        }
        missing = [k for k, v in required.items() if not v]
        if missing:
            raise ValueError(
                f"Отсутствуют обязательные переменные окружения: {', '.join(missing)}"
            )

        default_keywords = "AI,ИИ,искусственный интеллект,маркетинг,реклама,бренд,SEO,трафик,конверсия,бизнес,продажи,автоматизация,ChatGPT,OpenAI,агент"
        default_stopwords = "курс валют,спорт,шоу-бизнес,происшествия,выборы,погода"

        keywords_raw = os.getenv("KEYWORDS") or default_keywords
        stopwords_raw = os.getenv("STOPWORDS") or default_stopwords
        max_age_raw = os.getenv("MAX_NEWS_AGE_HOURS") or "24"
        interval_raw = os.getenv("CHECK_INTERVAL_MINUTES") or "60"

        posted_path = os.getenv("POSTED_JSON_PATH") or "posted.json"
        if not os.path.isabs(posted_path):
            posted_path = os.path.join(BASE_DIR, posted_path)

        api_base = (os.getenv("TELEGRAM_API_BASE") or "https://api.telegram.org").rstrip("/")

        return cls(
            TELEGRAM_BOT_TOKEN=required["TELEGRAM_BOT_TOKEN"],
            TELEGRAM_API_BASE=api_base,
            TELEGRAM_CHANNEL_ID=required["TELEGRAM_CHANNEL_ID"],
            RSS_URLS=required["RSS_URLS"],
            IMAGE_MODE=(os.getenv("IMAGE_MODE") or "pollinations").lower(),
            POSTED_JSON_PATH=posted_path,
            LOG_LEVEL=os.getenv("LOG_LEVEL") or "INFO",
            OPENAI_API_KEY=os.getenv("OPENAI_API_KEY") or "",
            OPENAI_IMAGE_MODEL=os.getenv("OPENAI_IMAGE_MODEL") or "gpt-image-1",
            OPENAI_IMAGE_SIZE=os.getenv("OPENAI_IMAGE_SIZE") or "1024x1024",
            KEYWORDS=[k.strip() for k in keywords_raw.split(",") if k.strip()],
            STOPWORDS=[k.strip() for k in stopwords_raw.split(",") if k.strip()],
            MAX_NEWS_AGE_HOURS=int(max_age_raw),
            CHECK_INTERVAL_MINUTES=int(interval_raw),
        )
