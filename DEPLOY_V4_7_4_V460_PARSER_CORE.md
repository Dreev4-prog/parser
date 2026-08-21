# DT Parser v4.7.4 — v4.6.0 Parser Core Restore

This release restores the foreground parser runtime exactly to the v4.6.0 core while retaining later user-facing features.

## Restored byte-for-byte from v4.6.0

- `parser.py`
- `date_manager.py`
- `date_worker.py`
- `page_manager.py`
- `page_worker.py`
- `view_manager.py`
- `view_counter_worker.py`

The scan orchestration functions in `bot.py` (`process_scan_job`, `scan_worker`, `distributed_scan_worker`) were verified to already match v4.6.0 and remain unchanged.

## Removed from the foreground scan path

- Chromium idle shutdown introduced after v4.6.0
- scan-fleet wake-up/prewarm introduced later
- Page Worker browser prewarm
- View Worker HTTP prewarm/session recovery experiments
- later View fast-recovery tuning
- later cold-start sharding changes beyond the v4.6.0 manager behavior

## Kept

- RU/EN user interface
- language selection on first start
- current admin panel and active-parser view
- DT AI Lab badge/inbox behavior
- Russian AI Lab labels
- Product Opportunity Engine
- v4.6.7 Fast UI user navigation

## Important trade-off

This intentionally restores v4.6.0 runtime behavior. Chromium is no longer closed by the v4.6.1+ 10-minute idle-memory mechanism, so idle RAM can return to the higher levels seen before v4.6.1. This is deliberate so parser speed can be compared against the known-good v4.6.0 baseline without any later runtime changes.

No PostgreSQL migration or new Railway variables are required.
