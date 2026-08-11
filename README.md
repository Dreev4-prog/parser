# Kleinanzeigen Parser Bot v2.2.0 — Smart Parsing

Telegram parser for public Kleinanzeigen category pages. v2.2 separates **collection** from **output**: the parser stores the full current-day dataset, while settings decide what goes into the export.

## New in v2.2

### ⚙️ Parsing/output settings

- **Output mode**
  - 🆕 Самые новые
  - 📚 Все
  - 💎 Уникальные (beta title-family heuristic)
  - 🔥 Часто публикуемые (aggregated product-family report)
  - 💰 Ниже рынка β (>=20% below the median of a product family, minimum 3 priced samples)
- **Smart duplicates** — likely reposts with the same normalized title + price + category are collapsed, keeping the newest.
- **Clean services/search** — filters obvious `Suche`, `Ankauf`, `Reparatur`, service/rental/swap noise from exports. This does not delete it from the DB.
- **Period** — 1h / 3h / 6h / today.
- **Price presets** — any / 0–50 / 50–100 / 100–200 / 200–500 / 500+ EUR.
- **Sort** — newest / price ascending / price descending.
- **Include words** — comma-separated allow-list keywords.
- **Exclude words** — comma-separated custom exclusions.

### 📦 Result export

The main menu now has **📦 Получить результат**. It generates a CSV using current settings without rescanning Kleinanzeigen.

`🔥 Часто публикуемые` exports:
- category
- product-family key
- example title
- publication count
- min / median / max price
- newest timestamp
- example link

`💰 Ниже рынка β` exports potential price outliers against the median of similar title families. It is a heuristic, not a valuation guarantee.

## Important behavior

- Collection is still by public category/search pages only.
- Filters affect exports, not collection. You can change settings and export again instantly.
- ID-based database deduplication remains permanent.
- Smart duplicate/family grouping is intentionally conservative and heuristic.
- No CAPTCHA/authentication/access-control bypass is implemented.

## Railway

Required variables:

```env
BOT_TOKEN=...
ADMIN_IDS=123456789
```

Recommended later for persistence:

```env
DATABASE_URL=postgresql://...
```

Optional parser variables:

```env
MAX_PAGES_PER_CATEGORY=500
PAGE_DELAY_SECONDS=0.7
STOP_AFTER_EMPTY_TODAY_PAGES=2
STOP_AFTER_NO_NEW_PAGES=2
```

Start command:

```bash
python bot.py
```

## Upgrade from v2.1

Replace the repository files with v2.2 and redeploy. The new `user_settings` table is created automatically. Existing listings/categories remain compatible.
