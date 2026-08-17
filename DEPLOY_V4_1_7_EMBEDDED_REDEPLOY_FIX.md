# DT PARSER v4.1.7 — Embedded Fleet Redeploy Fix

## Why v4.1.6 could show “Browser Fleet не запущен” after redeploy

The previous Railway process left its Redis worker heartbeat alive for about 20 seconds. The new process saw that stale key and assumed an external fleet already existed, so it skipped its own embedded worker. When the stale key expired, worker count became zero.

## v4.1.7 behavior

The `parser` service always starts one embedded browser reserve in distributed mode and writes its heartbeat before Telegram polling begins. External `fleet-worker` replicas are optional for a single-user test and can be added later for scale.

Required on the `parser` service:

```text
REDIS_URL=${{Redis.REDIS_URL}}
DATABASE_URL=${{Postgres.DATABASE_URL}}
BOT_TOKEN=...
```

Start command remains:

```text
python bot.py
```

Expected startup logs:

```text
Embedded Browser Fleet reserve online ...
Starting @DTTEAM_PARSER_BOT | mode=distributed ... embedded_fleet=True ...
```

No PostgreSQL migration is required.
