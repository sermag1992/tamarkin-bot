"""Разовая диагностика: кто бот, что за канал, webhook, и что висит в очереди."""
import asyncio
import logging
import os
import sys

import aiohttp

logging.basicConfig(level=logging.INFO)


async def api(http, token, method, params=None):
    url = f"https://api.telegram.org/bot{token}/{method}"
    async with http.get(url, params=params or {}, timeout=aiohttp.ClientTimeout(total=30)) as r:
        return await r.json()


async def main() -> int:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    channel = os.environ["TELEGRAM_CHANNEL_ID"]
    async with aiohttp.ClientSession() as http:
        me = await api(http, token, "getMe")
        u = me.get("result", {})
        print("БОТ: @" + str(u.get("username")), "| id =", u.get("id"))

        wh = await api(http, token, "getWebhookInfo")
        w = wh.get("result", {})
        print("WEBHOOK url:", repr(w.get("url")))
        print("WEBHOOK pending_update_count:", w.get("pending_update_count"))
        if w.get("last_error_message"):
            print("WEBHOOK last_error:", w.get("last_error_message"))

        chat = await api(http, token, "getChat", {"chat_id": channel})
        c = chat.get("result", {})
        print("КАНАЛ:", c.get("title"), "| тип:", c.get("type"), "| форум:", c.get("is_forum"))

        # Подглядываем в очередь обновлений БЕЗ offset — не подтверждаем ничего.
        # NB: если webhook установлен, getUpdate вернёт 409 — это тоже диагноз.
        upd = await api(http, token, "getUpdates", {"timeout": 0})
        if not upd.get("ok"):
            print("GET_UPDATES ОШИБКА:", upd.get("description"))
            return 1
        updates = upd.get("result", [])
        print("В ОЧЕРЕДИ обновлений:", len(updates))
        for u2 in updates:
            kind = "message" if "message" in u2 else "callback" if "callback_query" in u2 else list(u2.keys())
            if "message" in u2:
                m = u2["message"]
                sender = (m.get("from") or {}).get("username") or (m.get("from") or {}).get("first_name")
                print(f"  [{u2['update_id']}] message от {sender} в чате {m.get('chat', {}).get('type')}: {repr((m.get('text') or '')[:60])}")
            elif "callback_query" in u2:
                cq = u2["callback_query"]
                print(f"  [{u2['update_id']}] callback от {(cq.get('from') or {}).get('username')}: {cq.get('data')}")
            else:
                print(f"  [{u2['update_id']}] тип {kind}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
