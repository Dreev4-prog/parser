# DT Parser 4.21.8 — Full Runtime Audit

Audited from the 4.21.7 release archive.

## Critical fixes

1. Radar admin entry used database work before Telegram callback acknowledgement; a busy DB made the button appear dead.
2. Dashboard queried nonexistent ORM attribute `RadarProduct.demand_status`; the real aggregate state column is `RadarProduct.status`.
3. `radar_v3_expire_observations()` had an invalid nested SQLAlchemy UPDATE inside `.where()` and could fail in the live expiry scheduler.
4. Rearming quiet/expired observations did not clear Radar 3.1 evidence fields (`confidence`, percentile, acceleration, scored/strong streaks), allowing old evidence to contaminate a new baseline.
5. Category and family peer sets admitted recently expired observations, which could inflate percentile/repeatability and contribute to false Hot paths.
6. Radar 3.1 snapshots stayed live for the generic 48-hour product aggregation window. Old Strong/Hot evidence could resurrect after the intended six-hour observation TTL.

## Reliability/performance fixes

- Whole Radar control panel has bounded timeout/fallback and opens a loading shell immediately.
- Dashboard observation counts were consolidated to bounded aggregate reads.
- Explicit PostgreSQL indexes added for Radar 3.1 columns introduced by ALTER TABLE migrations.
- A new reset marker clears potentially contaminated 4.21.7 Radar state while preserving raw Listing/ViewHistory.
- Legacy AI Picks removed from visible Radar UI; old callback payloads remain compatible by redirecting to Rising.

## Static integrity checks

- All Python modules compile.
- ORM class attribute references match mapped model fields.
- Every inline callback payload has a matching exact/prefix handler.
- No duplicate exact/prefix callback filters detected.
