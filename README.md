# KladMapk — Render-ready

Файлы для загрузки в GitHub-репозиторий KladMapk-License.

## Render
Build Command:
`pip install -r requirements.txt`

Start Command:
`gunicorn app:app --bind 0.0.0.0:$PORT`

## Environment Variables
- `TELEGRAM_BOT_TOKEN` — токен Telegram-бота
- `TELEGRAM_ADMIN_ID` — ID администратора
- `BASE_URL` — адрес сервиса Render
- `WEBHOOK_SECRET` — секретная строка (можно оставить значение, сгенерированное Render)
- `LICENSE_DAYS` — срок лицензии, например `30`

`coords.json` — файл с координатами карты. Он уже включён в этот архив.
