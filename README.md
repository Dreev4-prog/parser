# Kleinanzeigen Parser Bot v2.7.1 — Stable Inline Views

This patch keeps inline public view counters from v2.7.0 and makes category scanning gentler when Kleinanzeigen temporarily refuses requests.

## Changes

- Inline views stay enabled during the normal category scan.
- Default view concurrency reduced from 8 to 5; global detail-page cap reduced from 10 to 6.
- View cache increased to 30 minutes, reducing repeat ad-page opens and extra view increments.
- Category page delay defaults to 1.0s.
- HTTP 403/429 gets a bounded cooldown + retry (no CAPTCHA/challenge/protection bypass).
- If the site still refuses a category, already collected rows are preserved and exported instead of failing the whole job.
- Interrupted categories are not marked as fully seeded, so a later run can try again.

## Optional Railway variables

```env
VIEW_COUNT_CONCURRENCY=5
VIEW_COUNT_GLOBAL_CONCURRENCY=6
VIEW_COUNT_CACHE_TTL_SECONDS=1800
PAGE_DELAY_SECONDS=1.0
CATEGORY_HTTP_RETRIES=3
CATEGORY_403_BACKOFF_SECONDS=10
CATEGORY_RETRY_JITTER_SECONDS=2
```

Start command remains:

```bash
python bot.py
```
