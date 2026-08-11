# Kleinanzeigen Parser Bot v2.6.9 — Passive View Counter Fast Path

This release makes public view-count collection faster and safer for analytics.

## What changed

- **Passive network capture:** while Chromium opens a normal public Kleinanzeigen ad page, the parser listens for the public `s-vac-inc-get.json` response that the page itself requests.
- **No extra endpoint call:** the parser does **not** request that counter endpoint directly. This avoids generating an additional counter request merely to read the number.
- Parses likely counter values from JSON/plain-text response shapes conservatively.
- If the passive network response yields a counter, it is used immediately.
- If the response shape changes or cannot be parsed, the existing DOM fallbacks remain (`#viewad-cntr-num`, extra-info block, alternate view metadata, hydration JSON).
- Heavy images/fonts/media are still blocked in Playwright to keep detail-page loading lighter.
- Default view enrichment concurrency is raised from 6 to **8** (still capped at 10).
- The **👁 Тест просмотров** screen now shows when the discovered endpoint was passively parsed and the extracted value/shape.
- Existing DB cache and `ViewHistory` remain unchanged.

## Important

The page load itself may be what Kleinanzeigen uses to count a view. v2.6.9 avoids making a **second/direct** counter request; it only observes the request already initiated by the public page.

No authentication, CAPTCHA bypass, challenge bypass, or protection bypass is implemented.

## Deploy

Replace the files in the same GitHub repository and redeploy Railway.

Start command:

```bash
python bot.py
```

Required variables remain:

```env
BOT_TOKEN=...
ADMIN_IDS=123456789
```

Optional tuning:

```env
VIEW_COUNT_CONCURRENCY=8
VIEW_COUNT_CACHE_TTL_SECONDS=900
```
