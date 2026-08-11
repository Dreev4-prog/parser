# Kleinanzeigen Parser Bot v1.2.0

Telegram-интерфейс для парсера публичных страниц категорий Kleinanzeigen.

## Что умеет v1.2

- `/start` — меню категорий.
- Категории: Konsolen, Notebooks, PCs, Handy & Telefon, TV & Video, Audio & HiFi.
- По нажатию на категорию читает первую страницу свежих объявлений.
- Сохраняет: категория, название, цена, ссылка, ID, дата первого/последнего обнаружения.
- Не сохраняет просмотры.
- Не дублирует объявления по ID.
- Кнопки `Последние` и `База`.
- SQLite для простого теста; `DATABASE_URL` от Railway PostgreSQL также поддерживается.

## Railway

1. Создай Telegram-бота через @BotFather и скопируй токен.
2. Замени файлы в текущем GitHub-репозитории файлами из этой версии.
3. Railway -> Variables -> добавь `BOT_TOKEN`.
4. Start Command: `python bot.py`.
5. После deploy открой Telegram-бота и отправь `/start`.

### PostgreSQL (рекомендуется позже)

Добавь PostgreSQL в Railway и передай приложению `DATABASE_URL`. Код автоматически преобразует стандартный Railway `postgresql://` URL в async SQLAlchemy URL.

## Ограничения

Парсер работает только с публичными HTTPS-страницами `kleinanzeigen.de`. Он не обходит CAPTCHA, логин или другие ограничения доступа.
