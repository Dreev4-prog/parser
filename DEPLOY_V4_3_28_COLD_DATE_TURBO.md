# Deploy v4.3.28 — Cold Date Turbo + Parallel Hidden Fill

## Railway changes

No new service is required.

Keep the existing architecture:

- Main Bot ×1
- Date Worker ×2
- Page Worker ×2
- View Worker ×2
- Redis
- PostgreSQL

The Date Worker still only needs `REDIS_URL` as its mandatory app variable.

## Built-in defaults

```env
DATE_WORKER_CONCURRENCY=4
DATE_WORKER_BROWSER_CONFIRM_CONCURRENCY=1
DATE_COLD_TURBO_ENABLED=1
HIDDEN_DATE_PREWARM_ENABLED=1
HIDDEN_DATE_PREWARM_WINDOW=6
HIDDEN_DATE_PREWARM_CONCURRENCY=4
```

Do not add these unless you want explicit overrides; the code already uses these defaults.

## Expected behaviour on a first 6–7-day-old scan

1. Date Worker fans out a broad page grid in one batch.
2. If the target is inside pages 1–50, the bracket is refined and locally verified.
3. If page 50 is still newer than the target, Main Bot verifies page 50 once and goes directly to regional depth.
4. While nationwide pages are collected, several regional date boundaries are already warming in Date Worker.
5. When regional depth begins, Telegram shows `Региональный добор даты`, and most regional locators should already be cached or in flight.

## Rollback

Disable only Cold Date Turbo:

```env
DATE_COLD_TURBO_ENABLED=0
```

Disable only regional prewarm:

```env
HIDDEN_DATE_PREWARM_ENABLED=0
```

Reduce Date Worker HTTP fan-out:

```env
DATE_WORKER_CONCURRENCY=2
```

These rollback switches do not require changing Page Worker or View Worker.
