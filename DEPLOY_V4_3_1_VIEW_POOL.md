# DT PARSER v4.3.1 — Multi-User Views Pool

## What changed
- Keeps the stable v4.3.0 three-worker / isolated-BrowserContext architecture.
- Date search and exact-view extraction logic are unchanged.
- Raises the process-wide foreground view lane from 3 to 6 (`MULTIUSER_VIEW_POOL_SIZE=6`).
- The default three scan workers therefore get up to two official-counter HTTP requests each at the same time.
- Category/date requests keep reserved global capacity, so view collection cannot occupy the whole network pool.
- Background 3/6/12h measurements still pause while foreground user scans are active.
- Chromium fallback remains governed by the existing global browser limit (default 1), so increasing the fast HTTP pool does not create a wave of browser pages.

## Railway
Recommended values:
```
STABLE_SINGLE_SERVICE_MODE=1
MULTIUSER_STABLE_MODE=1
MULTIUSER_LOCAL_WORKERS=3
MULTIUSER_VIEW_POOL_SIZE=6
SCAN_CATEGORY_HARD_TIMEOUT_SECONDS=1200
```

## Conservative rollback
Set `MULTIUSER_VIEW_POOL_SIZE=3` to restore the v4.3.0 process-wide view pressure without reverting code.
Set `MULTIUSER_STABLE_MODE=0` for the full v4.2.5 single-user lane.
