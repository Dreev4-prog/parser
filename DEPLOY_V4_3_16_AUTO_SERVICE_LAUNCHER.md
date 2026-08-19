# v4.3.16 AUTO SERVICE LAUNCHER

This release removes the need to configure a different Railway Start Command manually for the main bot and the dedicated View Worker.

## How it works

`railway.json` starts:

```text
python service_launcher.py
```

Railway automatically provides `RAILWAY_SERVICE_NAME`.

- Service named `View Worker` -> `view_counter_worker.py`
- Every other service -> `bot.py`

An optional `DT_SERVICE_ROLE=view-worker` or `DT_SERVICE_ROLE=bot` override exists for future use, but is not required.

## Railway services

### Main bot
Keep the existing variables, including:

```text
REMOTE_VIEW_WORKER_ENABLED=1
REDIS_URL=${{Redis.REDIS_URL}}
MULTIUSER_LOCAL_WORKERS=4
```

### View Worker
Only this variable is required:

```text
REDIS_URL=${{Redis.REDIS_URL}}
```

Name the service exactly:

```text
View Worker
```

No manual Custom Start Command is required.

## Expected logs

Main service:

```text
[service-launcher] ... role=bot target=bot.py
```

View Worker:

```text
[service-launcher] service='View Worker' role=view-worker target=view_counter_worker.py
```

Then the worker should publish its Redis heartbeat and the Telegram admin panel should show it online.

## Known-good core

`parser.py`, `traffic.py`, `stable_engine.py` and `scan_selection.py` are unchanged from v4.3.15 / the v4.3.8 known-good parsing core.
