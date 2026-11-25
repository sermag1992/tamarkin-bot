# Бот автопостинга для Telegram-канала «Тамаркин.Маркетинг»

Пайплайн: RSS-ленты → фильтр по ключевым словам → дедупликация (posted.json) →
обложка статьи из ленты (fallback: генерация через Pollinations.ai) →
пост в Telegram-канал (aiogram 3.x, HTML-разметка, ссылка на источник).

## Файлы

| Файл | Назначение |
|---|---|
| `config.py` | чтение настроек из переменных окружения / `.env` |
| `news_parser.py` | RSS, фильтры, дедупликация, обложки |
| `image_generator.py` | картинка: обложка RSS / Pollinations / OpenAI, retry-логика |
| `telegram_poster.py` | форматирование и отправка поста в канал |
| `main.py` | цикл или одиночный прогон (`CHECK_INTERVAL_MINUTES=0`) |
| `.github/workflows/bot.yml` | бесплатный запуск в GitHub Actions каждый час |

## Запуск в GitHub Actions (без VPN и без своего сервера)

1. Залейте этот репозиторий на GitHub (ветка `master`).
2. В настройках репозитория добавьте два секрета
   (Settings → Secrets and variables → Actions → New repository secret):
   - `TELEGRAM_BOT_TOKEN` — токен бота от @BotFather
   - `TELEGRAM_CHANNEL_ID` — ID канала, например `-1002594726352`
3. Воркфлоу срабатывает каждый час по расписанию, либо вручную:
   вкладка Actions → tamarkin-bot → Run workflow.
4. Бот должен быть администратором канала с правом публикации.

Трафик到你 сеть не идёт: всё выполняется на серверах GitHub, Telegram API
доступен оттуда напрямую. Дедупликация хранится в `posted.json` и
коммитится обратно в репозиторий после каждого опубликованного поста.

## Запуск локально (на ПК)

```powershell
pip install -r requirements.txt
copy .env.example .env   # заполнить TELEGRAM_BOT_TOKEN и TELEGRAM_CHANNEL_ID
python main.py
```

## Настройки (переменные окружения / .env)

| Переменная | По умолчанию | Описание |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | — | обязательно |
| `TELEGRAM_CHANNEL_ID` | — | обязательно |
| `RSS_URLS` | — | обязательно, ленты через запятую |
| `IMAGE_MODE` | `rss` | `rss` (обложка статьи) / `pollinations` / `openai` |
| `CHECK_INTERVAL_MINUTES` | `60` | период цикла; `0` — одиночный прогон |
| `MAX_NEWS_AGE_HOURS` | `24` | свежесть новостей |
| `KEYWORDS` | см. config.py | тематический фильтр, через запятую |
| `STOPWORDS` | см. config.py | отбраковка, через запятую |
| `OPENAI_API_KEY` | — | только для `IMAGE_MODE=openai` |
| `TELEGRAM_API_BASE` | `https://api.telegram.org` | сменный API-адрес (релей) |
| `POSTED_JSON_PATH` | `posted.json` | хранилище хэшей |

## Если пост не выходит

- Бот не админ канала → добавить в админы.
- Неверный токен → пересоздать у @BotFather, обновить секрет/`.env`.
- Смотреть лог: в Actions — вывод шага «Проверить новости и опубликовать пост»;
  локально — консоль и файл `bot.log`.
