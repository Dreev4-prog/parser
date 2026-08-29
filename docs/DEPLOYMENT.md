# Deploy DT Parser 4.20.0 — FINAL Unified 48H

## Required checkout consistency

Deploy every behavior-critical service from the same final 4.20.0 checkout.

1. **Parser** — required: unified 48H ranking, automatic DB migration, Fresh/Context scheduler, hard-stop/watchdog, integrity and Verified Organic Velocity scheduler.
2. **AI Worker** — required: evidence-adaptive scoring and clean historical evidence.
3. **Lifecycle Worker** — required/recommended for one-version integrity semantics.
4. **Page Worker** — required for audited promotion/card semantics and cache schema `v4200-core2-audit3`.
5. **Date Worker** — required/recommended from the same checkout for chronology/stable-page consistency.
6. **View Worker** — required healthy; exact counters and checkpoint verification depend on it.

`init_db()` performs the new Radar Rank/Demand Gate columns as additive migrations. **No manual SQL is required.**

## Before deploy

- Stop the current AutoScan from admin.
- Let/force Parser reach paused/idle.
- Do not delete PostgreSQL or Redis.
- Keep current Railway environment variables.
- Deploy Parser + Page + Date + View + AI + Lifecycle from the same checkout.

Do not pin pre-4.20 `PAGE_RUNTIME_PREFIX`, `DATE_RUNTIME_PREFIX` or `VIEW_RUNTIME_PREFIX`. The audited code uses `v4200-core2-audit3` and isolates incompatible old jobs/cache payloads.

Optional values keep safe defaults:

- `RADAR_AUTOSCAN_CATEGORY_TIMEOUT_SECONDS=480`
- `RADAR_AUTOSCAN_VIEW_RECOVERY_TIMEOUT_SECONDS=240`
- `AI_EARLY_MAX_AGE_HOURS=24`

## Expected first Parser startup

Parser should report version `4.20.0` and:

`v4.20.0 DT Radar Core 2.0 online`

It should also perform the one-time **Unified 48H ranking repair**. This is expected to make the live Hot/Strong lists temporarily smaller because old AutoScan/scan-hot synthetic ranking snapshots are removed. Favorites/product catalogue IDs are preserved. New Fresh/Context evidence repopulates live status.

## First functional test

1. Run a manual Fresh AutoScan.
2. Confirm live stage moves date -> pages -> exact views -> Organic Gate.
3. Verify telemetry shows `Early / Strong / Hot` separately.
4. Confirm a low-volume row such as 15 demand-safe views cannot appear in Hot.
5. Press Hard Stop during a category; confirm the same category remains for resume.
6. Resume; confirm the round advances.
7. After Fresh, Context should scan yesterday at most once for that Moscow day.
8. Context **may add Radar signals now**, but only rows passing demand provenance + unified Demand Gate. It is not expected to be `Radar +0` anymore.
9. Verify a yesterday Hot/Strong item can appear in the same public lists as a today item.
10. A first exact counter `>=400` must remain score-ineligible until two later clean checkpoints; only delta may then satisfy the gate.

## Rollback

4.20.0 uses additive DB state. Older code does not understand the unified Radar fields/context behavior, so stop AutoScan and roll back the whole behavior-critical fleet together. Do not mix Parser from this build with older ranking/runtime workers.
