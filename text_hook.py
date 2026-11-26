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


class HookWriter:
    """Переписывает заголовок и суть новости в цепляющий пост. Фолбэк — исходный текст."""

    def __init__(self, model: str = "openai", temperature: float = 0.7) -> None:
        self.model = model
        self.temperature = temperature

    def build_prompt(self, title: str, summary: str) -> str:
        return PROMPT_TEMPLATE.format(title=title, summary=summary)

    @retry_async(retries=3, base_delay=15.0)
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
