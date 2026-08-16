# DT PARSER v4.1.3 — Railway Start Command Fix

## Fixed
Railway was interpreting `PRIMARY_SCAN_INLINE_VIEWS=0` as the executable because it was embedded in `railway.json` `startCommand`.

The project now ships with:

```text
python bot.py
```

`PRIMARY_SCAN_INLINE_VIEWS=0` belongs only in Railway Variables.

## Required for Browser Fleet
The bot still needs the shared Redis connection:

```text
REDIS_URL=${{Redis.REDIS_URL}}
```

Add the same Redis reference to `parser`, `fleet-worker`, and `views-worker`.

Expected parser startup log after Redis is connected:

```text
mode=distributed ... redis=True ... local_workers=0
```

## Service commands
- parser: `python bot.py`
- fleet-worker: `python fleet_worker.py`
- views-worker: `python views_worker.py`
