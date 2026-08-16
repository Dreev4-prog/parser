# DT PARSER v4.1.4 — Auto-Distributed Railway

## What changed

The code no longer relies on a manually maintained `DISTRIBUTED_WORKERS` switch in the bot service. On Railway, a configured `REDIS_URL` automatically enables the Redis/browser-fleet architecture. A stale `DISTRIBUTED_WORKERS=0` is ignored.

## Required Railway services

- bot ×1 — `python bot.py`
- fleet-worker ×6 — `python fleet_worker.py`
- views-worker ×1 — `python views_worker.py`
- PostgreSQL ×1
- Redis ×1

The bot, fleet-worker and views-worker services must all reference the same `DATABASE_URL`, `REDIS_URL`, and `BOT_TOKEN`.

## Automatic safety

On Railway, if `REDIS_URL` is missing, the bot intentionally exits instead of falling back to local scan workers. This makes a configuration mistake visible immediately.

Correct bot startup log:

```text
Starting @DTTEAM_PARSER_BOT | mode=distributed source=redis-auto railway=True redis=True | local_workers=0 ...
```

If the log contains `mode=local`, the process is not running v4.1.4 or Railway was not detected.

## No new database migration

No PostgreSQL schema change is required.
