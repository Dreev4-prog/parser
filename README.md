# Kleinanzeigen Parser Bot v2.1.0

Incremental Telegram parser for public Kleinanzeigen category pages.

## What changed from v2.0

- Cumulative database: every listing is stored by Kleinanzeigen ad ID.
- First scan of the day can walk all pages containing `Heute`.
- Later scans stop after two consecutive pages with today's listings but no new IDs, so they normally do not rescan hundreds of old pages.
- New button: **📄 Выгрузить за сегодня**.
- CSV is cumulative for the current Berlin day and can be downloaded at any time.
- Scan status shows new listings and pages scanned.
- Built-in DB migration from v2.0 adds `category_key` and `posted_text`.
- Parent and child categories are not selected together, reducing duplicates.

## Railway variables

Required:

```env
BOT_TOKEN=...
ADMIN_IDS=123456789
```

Recommended for Railway:

```env
DATABASE_URL=postgresql://...
```

Without `DATABASE_URL` the bot falls back to SQLite. SQLite is enough for a quick test, but Railway's container filesystem is not a safe place for long-term cumulative data across redeploys/restarts. Use PostgreSQL (or a persistent volume) for reliable history.

Optional:

```env
MAX_PAGES_PER_CATEGORY=500
PAGE_DELAY_SECONDS=0.7
STOP_AFTER_EMPTY_TODAY_PAGES=2
STOP_AFTER_NO_NEW_PAGES=2
```

## Start command

```bash
python bot.py
```

## Workflow

1. `/start`
2. **🗂 Выбрать категории**
3. **▶️ Начать парсинг**
4. On the first run, the bot collects today's listings until `Heute` ends.
5. On later runs, it normally stops when it reaches already-stored listings.
6. **📄 Выгрузить за сегодня** generates the accumulated CSV at any time.

CSV columns:

- Категория
- Название
- Цена
- Дата публикации
- Ссылка

## Notes

The parser only reads publicly available Kleinanzeigen category/search pages. It does not bypass CAPTCHA, authentication, or access controls.
