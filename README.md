# Kleinanzeigen Parser Bot v2.4.0 — Smart Analytics

Telegram parser for public Kleinanzeigen category pages. Collection and analytics are separated: the parser stores the full current-day dataset, while output settings decide what goes into the export.

## What changed in v2.4

### Smarter product-family grouping
Titles are normalized before analytics:
- common aliases are unified (`PlayStation 5` / `PS 5` -> `ps5`, `Mac Book` -> `macbook`, etc.);
- storage notation is unified (`128 GB` -> `128gb`);
- seller/condition/color noise is reduced;
- model, variant, capacity and important product-type tokens are retained;
- token order no longer matters for grouping.

The grouping is deliberately conservative. It is a heuristic, not a catalogue/GTIN match.

### Analytics criteria
- **💎 Unique**: a normalized product family occurs exactly once in the selected period.
- **🔥 Frequently published**: at least 3 distinct listing IDs in one product family. Export includes count, min/median/max price and family confidence.
- **💰 Below market**: at least 5 priced listings in one family. A candidate must be at least 20% below the *leave-one-out* median of the other similar listings. Obvious 1 EUR placeholder/bait values are suppressed for higher-value families.
- **⚡ Fast disappearing**: disappeared listings with an observed upper lifetime of 12 hours or less. Export adds the detection window and confidence based on how recently the ad was last seen alive.
- **📉 Price drop**: the same listing ID dropped by at least 5 EUR and at least 5%.
- **🚫 Smart duplicates** remains stricter than product-family grouping: it collapses only very similar normalized title + price + category repetitions. Database ID deduplication remains permanent.

### Better scoping
Exports now respect the currently selected categories when categories are selected. Old data from unrelated categories no longer contaminates analytics.

### UI
Settings now include **ℹ️ Как работают режимы** with the current rules inside Telegram.

## Export price columns
Normal exports contain both:
- `Цена` — original display value (`350 € VB`, `Zu verschenken`, etc.);
- `Цена, €` — numeric value when available.

Analytics exports also contain confidence/quality fields where relevant.

## Railway
Required variables:

```env
BOT_TOKEN=...
ADMIN_IDS=123456789
```

Optional parser variables remain supported:

```env
MAX_PAGES_PER_CATEGORY=500
PAGE_DELAY_SECONDS=0.7
STOP_AFTER_EMPTY_TODAY_PAGES=2
STOP_AFTER_NO_NEW_PAGES=2
AVAILABILITY_CHECK_LIMIT=150
AVAILABILITY_CONCURRENCY=4
```

Start command:

```bash
python bot.py
```

## Upgrade from v2.3
Replace the repository files with v2.4 and redeploy. No new Telegram bot or Railway project is required.

SQLite still works for testing. PostgreSQL will be added later for durable long-term analytics/history on Railway.

## Important limitations
- Public Kleinanzeigen pages only.
- No CAPTCHA/authentication/access-control bypass.
- “Disappeared” does not prove “sold”; it is only a demand signal.
- Product-family grouping is heuristic and will be improved further with accumulated data.
