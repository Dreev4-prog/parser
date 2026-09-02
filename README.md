# DT Parser 4.21.14 — Radar 3.2 Startup Guard Fix

## Radar scope
- AutoScan: 20 pages, today only.
- Automatic Radar excludes Auto, Immobilien, Jobs, Dienstleistungen, Unterricht & Kurse and Nachbarschaftshilfe.
- The same exclusion policy is applied to user-scan Radar baselines.
- The normal user parser is unchanged; exclusions apply only to Radar analytics.

## Category-adaptive demand
- First exact view counter is baseline only and contributes 0 points.
- <3 views/hour is hard noise.
- Candidate = P90 of the current leaf category.
- Early / Score = P95.
- Strong = P98.
- Hot interval = P99, with persistence or an independent family confirmation required for Hot.
- First scored checkpoint remains capped at 50/100.
- Evaluation remains two-pass so one batch uses one shared category cohort.

## Live / History
- Six-hour expiry moves a product out of the live catalogue without destroying its last confirmed Score/peak.
- Historical rows do not appear in Hot / Rising / Best live lists.
- A historical product may return to live only after a new DT checkpoint confirms demand.

## 4.21.14 startup fixes
- Restores every omitted AutoScan runtime constant, including the category/view watchdogs, backoff values, safe view concurrency and launch watchdog.
- Invalid watchdog environment values fall back safely instead of crashing startup.
- Restores `_radar_autoscan_failure_list()` and removes the dead yesterday-Context branch that referenced a retired helper.
- `prepare_radar_v3_once()` is now permanently non-destructive in normal startup and maintenance. A missing reset marker is repaired without deleting Radar tables.
- Adds a global-symbol audit that fails the release if runtime code references an undefined module global.

## Important migration note
The 4.21.13 deployment shown in Railway executed the old Radar reset before crashing. That reset deleted Radar products/observations/snapshots while preserving Listing/ViewHistory. 4.21.14 will not perform another destructive reset; exact deleted pre-crash Radar scores cannot be reconstructed one-for-one, but new live Radar evidence can rebuild from fresh checkpoints.

## Validation
Run `python -m compileall -q .`, `pytest -q`, `python scripts/release_smoke.py`, and `python scripts/check_runtime_globals.py`.
