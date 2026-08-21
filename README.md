# DT Parser v4.7.3 — Performance Restore

Production-clean GitHub package.

## What changed in v4.7.3

This release restores the proven fast parsing path while keeping the useful idle-memory and worker-fleet improvements.

### Global idle shutdown

Chromium still closes after 10 minutes of true idle time, but the countdown now starts only when BOTH conditions are true:

- the local Date/Page/View worker queue is empty and has no active job;
- the global `dtparser:scan_jobs` stream is empty, meaning no foreground user scan is queued or still being processed.

This prevents Page/View Chromium from shutting down between stages of one long scan. The workers, Redis connections and heartbeats remain online.

### Fast exact views restored

The default view path is restored to the proven two-phase flow:

1. one exact request to Kleinanzeigen's official public counter;
2. only misses go to exact Chromium fallback.

Per-item transient HTTP retries are disabled by default and the later forced HTTP-session recovery pass is disabled by default. Both mechanisms remain available only as optional diagnostics/tuning switches if explicitly enabled.

Defaults:

- `ACCURATE_VIEW_TRANSIENT_HTTP_RETRIES=0`
- `ACCURATE_VIEW_SESSION_RECOVERY_ENABLED=0`

No guessed view counts are introduced.

### Retained improvements

- 10-minute Chromium memory release after the whole parser becomes idle;
- Date/Page/View scan-fleet wake-up;
- Page Worker Chromium prewarm;
- View Worker lightweight HTTP prewarm;
- cold-safe View sharding for the expected 4-replica fleet;
- up to 2 exact Chromium fallback navigations fleet-wide;
- v4.6.7 Fast UI behavior;
- Russian / English user interface;
- DT AI Lab and Product Opportunity Engine;
- admin workers / active parsing center.

## Railway

All services use the same repository and root `railway.json`:

```text
python service_launcher.py
```

`service_launcher.py` selects Bot / Date Worker / Page Worker / View Worker / AI Worker from the Railway service name or `DT_SERVICE_ROLE`.

No new required Railway variables or PostgreSQL migrations are needed for v4.7.3.

## Language

A new user chooses a language on the first `/start`:

- 🇷🇺 Русский
- 🇬🇧 English

The choice is saved per user and can later be changed in Settings or with `/language`. The admin panel remains Russian.
