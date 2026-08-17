# DT PARSER v4.3.0 — Multi-User Stable

## What changed
- Keeps `STABLE_SINGLE_SERVICE_MODE=1`: no Redis/distributed parser is required for this rollout.
- Runs 3 local scan workers by default (`MULTIUSER_LOCAL_WORKERS=3`).
- One long-lived Chromium process is shared by the Railway service.
- Every active user scan owns a separate isolated Playwright BrowserContext.
- Active category scans are NOT shared between users, so one slow date lookup cannot block another user.
- Background 3/6/12h view observations pause while foreground scans are active.
- A category has a 20-minute hard watchdog (`SCAN_CATEGORY_HARD_TIMEOUT_SECONDS=1200`); only that job context is recycled on timeout.
- v4.2.5 exact date/view logic is unchanged.

## First test
Start 3 scans from 3 devices at nearly the same time. Railway should log:
`v4.3.0 Multi-User Stable active | parser_lanes=3 | shared_chromium=True | isolated_context_per_job=True`
followed by `Scan worker #1/#2/#3 started`.

## Rollback
Set `MULTIUSER_STABLE_MODE=0` in Railway. The service immediately returns to one local scan worker and one-job-at-a-time behavior without reverting code.
