# Deploy DT Parser v4.23.16 on Railway

## Required variables

Configure real values in Railway Variables, never in Git:

- `BOT_TOKEN`
- `ADMIN_IDS`
- `DATABASE_URL`
- `REDIS_URL` when distributed Page/Date/View or Vinted workers are enabled

Start from `.env.example` for optional settings. Existing production variables can
remain unchanged for v4.23.16.

## Start command

The root `railway.json` and Docker image both start:

```text
python service_launcher.py
```

For the main Parser/Bot, omit `DT_SERVICE_ROLE` or set it to `parser`/`bot`. Dedicated
services may set an explicit role such as `page-worker`, `date-worker`, `view-worker`,
`lifecycle-worker`, `vinted-scan-worker`, `vinted-metrics-worker` or
`vinted-session-worker`.

Do not run the legacy AI service as an active scorer. If an old Railway AI service
still exists, the launcher safely sends it to `retired_ai_worker.py`.

## v4.23.16 rollout

1. Deploy the complete checkout to Parser / Bot.
2. Do not delete PostgreSQL or Redis and do not reset Radar observations.
3. Confirm the launcher log reports `version=4.23.16` and `target=bot.py`.
4. Confirm the bot reaches its normal startup line and four local parser lanes.
5. Open Radar analytics and verify checkpoint telemetry loads without SQL errors.
6. Run a small user scan and confirm it completes or reports a truthful partial result.

No SQL migration or new variable is required by this release. Existing Radar
observations are preserved and expire normally. Keeping all workers on the same
commit is recommended, but only Parser / Bot contains a runtime code change.

## Local verification before upload

```bash
python -m pip install -r requirements.txt pytest pytest-asyncio
python -m compileall -q .
python scripts/check_runtime_globals.py
python scripts/release_smoke.py
python -m pytest -q
```

## Rollback

Redeploy the previous complete checkout. v4.23.16 has no database migration and does
not transform stored Radar data, so rollback does not require SQL or data restoration.
