# Deploy DT Parser 4.20.0

## Required checkout consistency

Deploy every behavior-critical service from the same 4.20.0 checkout.

1. **Parser** — required. New AutoScan state policy, Fresh/Context scheduler, hard-stop/watchdog, integrity and Verified Organic Velocity scheduler.
2. **AI Worker** — required. Public initial-candidate freshness remains 24 hours; 48H Context still supplies age-matched market/history evidence.
3. **Lifecycle Worker** — required/recommended for one-version integrity semantics.
4. **Page Worker** — required for the audited promotion/card semantics and cache schema `v4200-core2-audit3`.
5. **Date Worker** — required/recommended from the same checkout for chronology and stable-page schema consistency.
6. **View Worker** — required healthy; exact counters and low-priority checkpoints depend on it.

No manual SQL migration is introduced by 4.20.0. Existing additive 4.15.x migrations remain handled by `init_db()`.

## Before deploy

- Stop the current AutoScan from the admin panel.
- Let/force the Parser reach paused/idle state.
- Do not delete PostgreSQL or Redis.
- Keep current Railway environment variables.

For this audited build, deploy **Parser + Page + Date + View + AI + Lifecycle from the same checkout**.
Do not pin old `PAGE_RUNTIME_PREFIX`, `DATE_RUNTIME_PREFIX` or `VIEW_RUNTIME_PREFIX` values from pre-4.20 deployments. The audited code uses the `v4200-core2-audit3` marker and ignores incompatible legacy overrides to prevent rolling-deploy cross-talk.

Optional values keep safe built-in defaults:

- `RADAR_AUTOSCAN_CATEGORY_TIMEOUT_SECONDS=480`
- `RADAR_AUTOSCAN_VIEW_RECOVERY_TIMEOUT_SECONDS=240`
- `AI_EARLY_MAX_AGE_HOURS=24`

## Expected startup

Parser should report version `4.20.0` and a banner containing:

`v4.20.0 DT Radar Core 2.0 online`

Normal scan workers should start and polling should begin.

## First functional test

1. Run a manual Fresh AutoScan.
2. Confirm live stage moves through date/pages/views/Organic Gate.
3. Press Hard Stop during a category and confirm the same category is retained for resume.
4. Resume and confirm the round advances.
5. After either a completed manual or daily Fresh Layer, Context Layer should run yesterday at most once for that Moscow calendar day.
6. Context completion should show `Radar +0` from context itself; its role is market evidence, not direct yesterday-total admission.
7. A first exact counter `>=400` must remain score-ineligible until two later clean checkpoints.

## Rollback

4.20.0 uses additive/persisted state. Rolling code back is possible, but older code does not understand the new `context` mode/layer semantics. Prefer stopping AutoScan first and rolling back the whole behavior-critical fleet together rather than mixing versions.
