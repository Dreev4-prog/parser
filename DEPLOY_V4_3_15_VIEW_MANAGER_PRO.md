# DT PARSER v4.3.15 — VIEW MANAGER PRO

Built directly on v4.3.14 / known-good v4.3.8 parser core.

## What is deliberately unchanged

`parser.py`, `traffic.py`, date search and exact-view extraction are not rewritten.
The dedicated worker still calls the exact v4.3.8 `fetch_public_view_counts()` implementation.

## What v4.3.15 adds

- Adaptive dedicated view lane: starts at 12, can grow to 16, backs off toward 8.
- Backoff reacts to 403/429, TrafficManager penalties, excessive browser fallback, and unknown results.
- Independent heartbeat task keeps the worker visible even during a long view round.
- Per-worker Redis heartbeat/status keys, ready for multiple Railway replicas later.
- Redis Streams partial-result checkpoints. A restarted/reclaimed worker resumes remaining URLs instead of repeating the whole batch.
- XAUTOCLAIM crash recovery + active-claim refresh so healthy long jobs are not stolen.
- Round timeout, parser self-reset, stalled-job requeue, and final automatic local v4.3.8 fallback if remote retries are exhausted.
- Admin panel button `👁 View Worker` with live queue, pool, views/sec, exact/fallback percentages, 403/429, cooldown, requeues and errors.

## Main bot variables

Keep the working v4.3.14/v4.3.8 setup:

```env
MULTIUSER_LOCAL_WORKERS=4
REMOTE_VIEW_WORKER_ENABLED=1
REDIS_URL=<same Railway Redis private URL/reference>
VIEW_REDIS_PREFIX=dtparser:viewcounter
VIEW_REMOTE_TIMEOUT_SECONDS=1800
STABLE_SINGLE_SERVICE_MODE=1
MULTIUSER_STABLE_MODE=1
```

No new main-bot variable is required.

## View Worker variables

Recommended:

```env
REDIS_URL=<same Railway Redis private URL/reference>
VIEW_REDIS_PREFIX=dtparser:viewcounter

VIEW_WORKER_POOL_MIN=8
VIEW_WORKER_POOL_DEFAULT=12
VIEW_WORKER_POOL_MAX=16
VIEW_WORKER_ADAPTIVE_ENABLED=1
VIEW_WORKER_ADAPTIVE_HEALTHY_ROUNDS=2
VIEW_WORKER_ADAPTIVE_BACKOFF_SECONDS=8

VIEW_WORKER_BROWSER_POOL_SIZE=2
VIEW_WORKER_VIEW_MIN_INTERVAL_SECONDS=0.05
VIEW_WORKER_ROUND_SIZE=48
VIEW_WORKER_MAX_ACTIVE_JOBS=8

VIEW_WORKER_ROUND_TIMEOUT_SECONDS=180
VIEW_JOB_TIMEOUT_SECONDS=180
VIEW_JOB_REQUEUE_ENABLED=1
VIEW_WORKER_MAX_REQUEUES=2
VIEW_WORKER_RECLAIM_IDLE_MS=180000
```

The old v4.3.14 variable `VIEW_WORKER_VIEW_POOL_SIZE=12` remains backward-compatible as the starting pool if `VIEW_WORKER_POOL_DEFAULT` is absent, but it is cleaner to remove it and use the three new MIN/DEFAULT/MAX variables.

## Worker start command

```text
python view_counter_worker.py
```

## Expected log behavior

Startup:

```text
DT PARSER dedicated view worker online | ... pool=12 [8..16] ... adaptive=True
```

Healthy rounds can show:

```text
View round done ... pool=13 ... adaptive=healthy: 12->13
```

If 403/429 or traffic penalty appears, the pool automatically drops instead of continuing to increase pressure.

## Admin telemetry

Open:

`Админ-панель -> 👁 View Worker`

It shows live worker health, Redis queue depth, active jobs, current adaptive pool, browser pool, views/sec, exact %, browser-fallback %, 403/429, cooldown, requeues and worker errors.

## Rollback

Fastest rollback is still only on the main bot:

```env
REMOTE_VIEW_WORKER_ENABLED=0
```

The bot immediately returns to the unchanged local v4.3.8 view path.
