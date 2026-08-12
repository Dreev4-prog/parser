# Kleinanzeigen Parser Bot v3.0.7

## Popularity Tracker

v3.0.7 keeps the v3.0.6 exact-date/pagination mechanics and adds category-separated popularity analytics.

### Popular now

`🔥 Популярное сейчас` no longer mixes unrelated products. It shows only categories the user has already scanned. Inside each category:

- `👁 Самые просматриваемые` — total current views;
- `🚀 TOP 1ч`;
- `🚀 TOP 3ч`;
- `🔥 TOP 6ч`;
- `🔥 TOP 12ч`;
- `📈 TOP 24ч`.

Growth TOPs are sorted by **absolute new views gained** in the selected interval. Telegram shows TOP-10. `📊 Скачать TOP-50` creates an XLSX table containing initial/current views, real increase, views/hour, price, model, publication date, ID and direct link.

### Automatic secondary measurements

Every completed scan gets persistent checkpoints at `+1 / +3 / +6 / +12 / +24` hours. The schedule is stored in the database, so a normal bot restart does not erase future checkpoints. Each checkpoint refreshes only the listing IDs already belonging to that saved scan; it does not rescan category pages.

When a checkpoint finishes, the user gets a short Telegram notice. The resulting TOP is viewed from `🔥 Популярное сейчас` and is separated by category even when the original scan included several categories.

If Railway is offline long enough to miss the configured time window, the bot marks the checkpoint as missed instead of presenting a late measurement as an exact `+N h` result.

### Scan card

`📊 Мои сканы` now shows automatic checkpoint state (`1ч / 3ч / 6ч / 12ч / 24ч`). Manual `👁 Обновить просмотры` remains available and continues to add history points, while scheduled checkpoints are used preferentially for the exact TOP periods.

### Deployment

Railway start command remains:

```text
python bot.py
```

Existing variables continue to work. Optional new variables:

```text
OBSERVATION_POLL_SECONDS=30
OBSERVATION_CONCURRENCY=2
OBSERVATION_LATE_GRACE_MINUTES=45
```

`openpyxl` is included in `requirements.txt` for the downloadable TOP-50 XLSX table.
