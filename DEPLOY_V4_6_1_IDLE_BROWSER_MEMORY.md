# DT PARSER v4.6.1 — Idle Browser Memory

This release is a memory-only infrastructure update on top of v4.6.0 Product Opportunity Engine.

## What changes

Page Worker, Date Worker and View Worker no longer keep a process-local Chromium alive forever after it has been used.

The rule is deliberately conservative:

1. While this worker fleet has any Redis stream entry (queued **or currently claimed/processing**), no idle countdown exists.
2. When the stream is completely empty and the local replica has zero active work, a 10-minute warm-idle countdown starts.
3. Any new task cancels/resets that countdown.
4. After 10 uninterrupted idle minutes, the worker rechecks the queue and local active count under an activity lock.
5. Only then is the shared Chromium/Playwright runtime closed. Redis, PostgreSQL connections, worker heartbeat and the Python worker process stay online.
6. The next browser-requiring job lazily starts Chromium again. Long-lived parser objects detect the new browser generation and discard stale BrowserContext/Page references automatically.

This means a scan is never interrupted to save memory. The only expected cost is a small Chromium cold-start delay on the first browser request after a long idle period.

## Scope

Applied to:

- Page Worker
- Date Worker
- View Worker

Not changed:

- worker replica counts / concurrency
- scan/date algorithms
- exact view semantics
- DT AI Lab / Product Opportunity Engine
- PostgreSQL schema
- Redis job semantics
- ordinary Telegram scan UI

## Railway

No new required variables and no new services.

Default warm-idle timeout: **600 seconds (10 minutes)**.

Optional tuning variables exist only if needed later:

```env
PAGE_WORKER_BROWSER_IDLE_SECONDS=600
DATE_WORKER_BROWSER_IDLE_SECONDS=600
VIEW_WORKER_BROWSER_IDLE_SECONDS=600
```

Keep the current Page/Date/View replica counts. Deploy the same v4.6.1 commit to all services.

## Expected logs

After browser use finishes and the fleet becomes completely idle:

```text
Page Worker Chromium warm-idle countdown started | timeout=600s
```

After 10 uninterrupted idle minutes:

```text
Page Worker Chromium closed after 600s of complete fleet idle
```

The same messages exist for Date Worker and View Worker.
