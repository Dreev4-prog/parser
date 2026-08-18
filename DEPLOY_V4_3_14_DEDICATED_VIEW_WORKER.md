# DT PARSER v4.3.14 — Dedicated View Manager + Railway View Worker

This release starts from the known-good **v4.3.8** tree.

## Safety rule

`parser.py` is unchanged byte-for-byte from v4.3.8. Date search, exact view extraction,
HTTP counter parsing and Chromium fallback are not rewritten.

The only architectural change is **where exact-view batches execute**:

- Main bot: scans category/date pages exactly as v4.3.8.
- Redis: transports view-batch job IDs/progress/results.
- Dedicated View Counter Worker: runs the same v4.3.8 `parser.py` on a separate Railway service.
- Worker View Manager fair-interleaves active scans. One scan can consume the full lane; four scans share it.
- If the worker/Redis is unavailable before a job starts, the main bot automatically uses the unchanged local v4.3.8 path.

## Why this helps 4 simultaneous users

In v4.3.8 `traffic.py` intentionally caps the **local** view lane at 2 when 4 scan jobs are active.
The dedicated view service has zero category scan jobs, so its own view lane stays independent.
It also gets separate Railway CPU/RAM from the bot service.

## Railway layout

Create/keep three resources in one project:

1. `dt-parser-bot` — existing service, start command `python bot.py`
2. Railway Redis — shared queue only
3. `dt-parser-view-counter` — new service from the same repository, start command:
   `python view_counter_worker.py`

Both bot and view-counter service need the same `REDIS_URL`.

### Main bot variables

Keep all working v4.3.8 variables. Add:

```env
MULTIUSER_LOCAL_WORKERS=4
REMOTE_VIEW_WORKER_ENABLED=1
REDIS_URL=<Railway Redis private URL/reference>
VIEW_REDIS_PREFIX=dtparser:viewcounter
VIEW_REMOTE_TIMEOUT_SECONDS=1800
```

Do **not** enable the old distributed parser fleet. Keep:

```env
STABLE_SINGLE_SERVICE_MODE=1
MULTIUSER_STABLE_MODE=1
```

### View-counter worker variables

```env
REDIS_URL=<same Railway Redis private URL/reference>
VIEW_REDIS_PREFIX=dtparser:viewcounter
VIEW_WORKER_VIEW_POOL_SIZE=12
VIEW_WORKER_BROWSER_POOL_SIZE=2
VIEW_WORKER_VIEW_MIN_INTERVAL_SECONDS=0.05
VIEW_WORKER_ROUND_SIZE=48
VIEW_WORKER_MAX_ACTIVE_JOBS=8
```

No Telegram bot token and no PostgreSQL connection are required by `view_counter_worker.py`.

### Worker start command

Use either the service setting:

```text
python view_counter_worker.py
```

or Railway config file `railway.view-counter-worker.json`.

## First test

1. Deploy worker first and confirm log contains `DT PARSER dedicated view worker online`.
2. Only then set `REMOTE_VIEW_WORKER_ENABLED=1` on the bot and redeploy bot.
3. Run one 25-page scan. Worker log should show `View job admitted` and `View job complete`.
4. Run 4 scans simultaneously. Main bot stays on page scanning while the view service handles the exact counters.

## Instant rollback

Set on the main bot:

```env
REMOTE_VIEW_WORKER_ENABLED=0
```

Redeploy. The bot immediately returns to the exact local v4.3.8 view path. No code rollback is required.
