# DT PARSER v4.1.6 — Queue Visibility + Deferred Views

This release fixes the situation where Railway logs show real parsing while Telegram stays on `Подготавливаю скан · 0 сек`.

## Changes

- Distributed queued scans now update every ~3 seconds and show queue position + active worker count.
- Queued scan cards older than 10 minutes are retired at startup so old test jobs do not block the new embedded Fleet worker.
- A `100+ views` filter no longer fetches view counts after every category page. The crawler first traverses the requested date/pages, then performs one concurrent `Собираю просмотры` phase before the user's filter is frozen into the saved result.
- Added an INFO log when a Redis job is actually claimed, including scan/user/chat/message ids.
- No PostgreSQL migration is required.

## Railway

No new required variables. Defaults:

```env
DISTRIBUTED_STALE_QUEUE_SECONDS=600
DISTRIBUTED_QUEUE_UI_SECONDS=3
```

Keep `REDIS_URL` and `DATABASE_URL` connected.
