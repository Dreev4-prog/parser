# DT PARSER v4.1.5 — Self-Bootstrap Fleet

## Minimal Railway setup for the first stable scan

The main `parser` service only needs:

- Start Command: `python bot.py`
- `BOT_TOKEN`
- `DATABASE_URL=${{Postgres.DATABASE_URL}}`
- `REDIS_URL=${{Redis.REDIS_URL}}`
- `PRIMARY_SCAN_INLINE_VIEWS=0`

With Redis connected, v4.1.5 starts **one embedded Browser Fleet lane automatically** when no external parser/fleet worker is online. You do not need to create a separate `fleet-worker` service just to verify one-user parsing.

Expected parser log:

`mode=distributed ... redis=True ... embedded_fleet=True`

and:

`Embedded Browser Fleet fallback online ... transport=browser`

## Later: multi-user scaling

When the single-user scan is stable, create a `fleet-worker` service from the same code with Start Command:

`python fleet_worker.py`

Give it the same `REDIS_URL`, `DATABASE_URL`, and `BOT_TOKEN`, then scale replicas gradually. External fleet workers share the Redis queue with the embedded reserve lane.

To disable the embedded reserve after dedicated workers are proven stable:

`EMBEDDED_FLEET_FALLBACK=0`
