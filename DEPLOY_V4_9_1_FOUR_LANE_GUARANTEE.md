# DT PARSER v4.9.1 — Four-Lane Queue Guarantee

## Why this release exists

v4.9.0 already intended to run four user scans at once, but the main parser mode was still selected from Railway environment variables. A stale mode value could therefore put the Telegram service onto the Redis/distributed parser path, where `MULTIUSER_LOCAL_WORKERS=4` no longer controlled user-facing parser capacity. The admin screen could then show a pattern such as `2 active / 2 queued` even though Date/Page/View helper replicas were healthy.

v4.9.1 makes the intended production contract code-owned instead of configuration-owned.

## Guaranteed behavior

For the main Railway service started with:

```bash
python bot.py
```

the code now guarantees:

```text
users 1–4  -> local scan-worker-1..4 -> running
user 5+    -> scan_queue FIFO        -> queued until a lane is free
```

Trial and paid scans share this exact queue. There is no two-user trial limit.

The main bot pins before importing `distributed.py`, `traffic.py`, or `parser.py`:

```env
STABLE_SINGLE_SERVICE_MODE=1
MULTIUSER_STABLE_MODE=1
MULTIUSER_LOCAL_WORKERS=4
```

`MAX_CONCURRENT_JOBS` is then hard-set to four in Stable Single Service mode. Stale Railway values cannot silently lower the number of user consumers.

## Truthful admin status

A local worker now changes the matching `user_scans.status` row from `queued` to `running` immediately when it claims the job, before parser/browser setup. `👀 КТО СЕЙЧАС ПАРСИТ` therefore reflects actual ownership of all four lanes. The headline also shows `running/4`.

## Fail-fast safety

At startup the main bot verifies that:

- Stable Single Service is active;
- distributed foreground scan execution is off in the Telegram service;
- Multi-User Stable is active;
- exactly four user lanes are configured;
- exactly four `scan-worker-*` tasks are created.

If a future code change breaks that contract, the parser process exits visibly instead of silently running at reduced capacity.

Expected startup lines include:

```text
Starting @... | version=4.9.1 | mode=local ... local_workers=4 ...
v4.3.2 Multi-User Stable active | parser_lanes=4 ...
Scan worker #1 started
Scan worker #2 started
Scan worker #3 started
Scan worker #4 started
v4.9.1 Four-Lane Queue Guarantee online | parser_lanes=4 | fifth_plus=FIFO | trial_and_paid_same_queue=True ...
```

## Railway deployment

Redeploy only the main `parser` service from the v4.9.1 code first. No PostgreSQL migration is required. No new required Railway variable is required.

Keep the existing helper services unchanged:

- Date Worker × existing replicas
- Page Worker × existing replicas
- View Worker × existing replicas
- AI Worker as already configured
- Redis and PostgreSQL unchanged

The helper entrypoints explicitly set `STABLE_SINGLE_SERVICE_MODE=0` inside their own process, so the bot-level four-lane pin does not convert them into local user parsers.

## Smoke test

1. Redeploy `parser`.
2. Confirm the five startup lines above.
3. Start four scans from four accounts within a short window.
4. Admin `КТО СЕЙЧАС ПАРСИТ` should reach `4/4` active and `0` queued.
5. Start a fifth account. It should show `4/4` active and `1` queued.
6. Let one active scan finish; the fifth should automatically become `running`.
7. Repeat with a mix of free-trial and paid accounts; capacity must remain the same.

## Scope

This is a queue/orchestration hardening release. Date discovery, page collection, accurate views, regional coverage, filtering, TOP calculations, exports, and the v4.9.0 free-trial product rules are otherwise unchanged.
