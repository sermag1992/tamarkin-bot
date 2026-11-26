"""Разовая диагностика: кто бот и что за канал. Запускается через diagnostics.yml."""
import asyncio
import logging
import os
import sys

import aiohttp
from aiogram import Bot

logging.basicConfig(level=logging.INFO)


async def main() -> int:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    channel = os.environ["TELEGRAM_CHANNEL_ID"]
    async with aiohttp.ClientSession() as http:
        async with http.get(
            f"https://api.telegram.org/bot{token}/getMe", timeout=aiohttp.ClientTimeout(total=30)
        ) as r:
            me = await r.json()
            if not me.get("ok"):
                print("getMe FAIL:", me)
                return 1
            u = me["result"]
            print("БОТ: id =", u.get("id"))
            print("БОТ: username = @" + str(u.get("username")))
            print("БОТ: имя =", u.get("first_name"))
        async with http.get(
            f"https://api.telegram.org/bot{token}/getChat",
            params={"chat_id": channel},
            timeout=aiohttp.ClientTimeout(total=30),
        ) as r:
            chat = await r.json()
            if not chat.get("ok"):
                print("getChat FAIL:", chat)
                return 1
            c = chat["result"]
            print("КАНАЛ: id =", c.get("id"))
            print("КАНАЛ: название =", c.get("title"))
            print("КАНАЛ: тип =", c.get("type"))
            print("КАНАЛ: темы (is_forum) =", c.get("is_forum"))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
