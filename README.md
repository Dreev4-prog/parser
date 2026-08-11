# Kleinanzeigen Parser Bot v2.3.0 — Smart Analytics

Telegram parser for public Kleinanzeigen category pages. Collection and output are separated: the parser stores the current-day dataset, while filters/analytics decide what is exported.

## Fixed in v2.3

- Prices: more robust extraction from category cards + text fallback.
- Existing v2.2 rows with missing prices are backfilled during the next scan.
- Every regular export now has both `Цена` and numeric `Цена, €` columns.
- Analytics modes are visible directly inside **⚙️ Настройки парсинга**:
  - 🆕 Самые новые
  - 📚 Все
  - 💎 Уникальные
  - 🔥 Часто публикуемые
  - ⚡ Быстро исчезающие
  - 💰 Ниже рынка
  - 📉 Снижение цены

## Analytics

### 🔥 Часто публикуемые
Groups similar product titles and shows publication count plus example/minimum/median/maximum prices.

### ⚡ Быстро исчезающие
On export, checks a bounded batch of saved public listing links and records listings that are no longer available. The file includes approximate lifetime from first detection to the check that detected disappearance. This gets more useful after several runs during the day.

Defaults:

```env
AVAILABILITY_CHECK_LIMIT=150
AVAILABILITY_CONCURRENCY=4
```

### 💰 Ниже рынка
Heuristic grouping by normalized product titles. A listing is shown when it is at least 20% below the median of a group with enough priced samples.

### 📉 Снижение цены
v2.3 starts storing price history. When a known ad is parsed again with a lower price, it becomes available in this report. Existing v2.2 data does not have historical prices retroactively, so this report needs at least two observations after upgrading.

## Railway update

No new bot or Railway project is needed.

1. Replace repository files with v2.3.
2. Keep Railway Start Command:

```bash
python bot.py
```

3. Keep variables:

```env
BOT_TOKEN=...
ADMIN_IDS=...
```

4. `DATABASE_URL` is still optional for testing, but PostgreSQL is strongly recommended before relying on multi-day price/disappearance history.
5. After deploy, run `/start` and perform one new scan. The first v2.3 scan may traverse more pages because it also backfills missing prices from v2.2.

## Notes

The project reads publicly visible listing data and does not implement CAPTCHA bypass or protection evasion. Keep request rates reasonable.
