# Kleinanzeigen Parser Bot v2.7.0 — Inline Views Pipeline

This release moves public view-count collection into the main category scan.

## What changed

- Every parsed category page is followed immediately by view-count enrichment for the listings on that page.
- The parser passively reads the public `s-vac-inc-get.json` response requested by the normal ad page.
- DOM (`#viewad-cntr-num` and fallbacks) remains a backup.
- The result export no longer starts a separate “collecting views” phase; it only reads values already stored during the scan.
- Recent counters are reused from the DB cache (`VIEW_COUNT_CACHE_TTL_SECONDS`, default 15 minutes).
- A single Playwright browser/context is reused per category worker.
- Images, fonts, media and stylesheets are blocked for lighter detail-page loads.
- Batch collection skips the redundant static detail-page HTTP request and goes directly to the lightweight browser path.
- Local concurrency defaults to 8, with a global cap of 10 view pages across simultaneous jobs in one container.
- Live Telegram progress now includes how many view counters are already ready.
- `ViewHistory` continues to store changed counters for future growth analytics.

## Important

Opening a public ad page can itself add a view on Kleinanzeigen. The cache limits repeated openings, but a scan can still increment each checked listing roughly once when its counter is refreshed.

No authentication, CAPTCHA bypass, challenge bypass or protection bypass is implemented.

## Railway

Start command remains:

```bash
python bot.py
```

Required variables:

```env
BOT_TOKEN=...
ADMIN_IDS=123456789
```

Optional tuning:

```env
VIEW_COUNT_CONCURRENCY=8
VIEW_COUNT_GLOBAL_CONCURRENCY=10
VIEW_COUNT_CACHE_TTL_SECONDS=900
```
