# Kleinanzeigen Parser Bot v2.6.5.1 — Railway Playwright build fix

Исправление v2.6.5 для Railway.

## Что изменено

- Используется официальный Playwright Python Docker image:
  `mcr.microsoft.com/playwright/python:v1.61.0-noble`.
- Версия Python-пакета Playwright синхронизирована с Docker image: `playwright==1.61.0`.
- Удалён тяжёлый build-step `playwright install --with-deps chromium` из `python:3.12-slim`.
- Chromium и системные зависимости уже присутствуют в базовом Playwright image.
- Функция `👁 Тест просмотров` из v2.6.5 сохранена.

## Railway

Ничего нового создавать не нужно. Замените файлы в текущем GitHub-репозитории.

Start Command:

```bash
python bot.py
```

Переменные остаются прежними (`BOT_TOKEN`, `ADMIN_IDS` и существующие опциональные настройки).
