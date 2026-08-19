# DT PARSER v4.3.22 — STREAMING PAGE WORKER

Fix for the visible pause at `0/N pages` introduced by v4.3.21.

## What changed

- Page Worker prefetch is now **non-blocking/streaming**.
- Main scan no longer waits for the complete 15/25/50-page remote batch.
- Missing pages are claimed/enqueued with Redis pipelines, so 50 pages are dispatched in a few Redis round trips instead of sequential per-page round trips.
- The foreground scan waits only briefly (default max 1800 ms) for the **next** page if a remote worker already owns it.
- If that page is not ready in the small wait budget, the proven local parser fetches it immediately.
- A locally fetched fallback page is written into the same 180-second Redis cache, so other users and not-yet-started remote jobs can reuse it.
- Page Worker, parser core, traffic core, View Worker and View Sharding algorithms are unchanged.

## Expected UI behavior

Old v4.3.21:

`0/50` -> long wait until remote prefetch completed -> fast processing.

v4.3.22:

`0/50` -> first confirmed page -> `1/50` -> pages continue while Page Worker replicas warm the upcoming range in parallel.

## Railway

No new variables are required.

Existing services remain:
- Main bot
- View Worker (2 replicas)
- Page Worker (2 replicas)
- Redis/Postgres

Optional tuning only:
- `PAGE_CACHE_WAIT_MS=1800`
- `PAGE_CACHE_WAIT_POLL_MS=100`

Do not add these unless tuning is needed; the defaults are built in.
