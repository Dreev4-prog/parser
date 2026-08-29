# DT Radar 3.0 — Observed Demand

Radar 3.0 stops trusting the publication age and the counter already present when DT first sees an ad. Hidden bumps can make an old listing look new while preserving old views, so neither value is allowed to create a signal.

## Core contract

1. AutoScan still covers one Unified 48H circle: 15 pages today + 15 pages yesterday per category.
2. The first exact view count is baseline-only. It contributes zero points and cannot create Early, Strong, or Hot.
3. The first background remeasurement is due after 60 minutes.
4. Only `current_views - DT_baseline_views` and later interval deltas are demand evidence.
5. Early requires one positive DT-observed interval.
6. Strong requires two consecutive positive intervals on one listing OR observed growth on at least two independent listings in the same product family.
7. Hot requires persistent observed growth on at least two independent listings in the same product family.
8. Old Radar products/snapshots/favorites/lifecycle watches are purged once on first Radar 3.0 startup. Raw Listing and ViewHistory data are preserved for audit only.
9. Legacy scan-hot, AI and verified-velocity admission paths are disabled. They cannot repopulate Radar.
10. Old historical Radar backfill is disabled.

## Observed Score

The public score is now evidence from DT observation, not a reconstruction of listing age:

- 60 points: current observed velocity percentile inside the category.
- 25 points: persistence across DT checkpoints.
- 15 points: repeatability across independent listings of the same product family.

The initial counter never participates in this score.

## Observation lifecycle

- baseline -> first check after 60 minutes
- no positive growth -> quiet; stop spending traffic on it
- positive growth -> observed / Early; recheck after 60 minutes
- second consecutive positive interval -> confirmed / Strong
- two independent persistent listings -> Product Hot
- stale Radar products are moved to historical after six hours without a new observed signal
- quiet/expired observations can be re-armed by a later Radar circle after a cooldown, using a new baseline


## v4.21.1 — single observation owner / deadlock fix

- The legacy `AI Early Winner` / `dt-demand-score-v2.1-evidence-adaptive` pipeline is retired and cannot be re-enabled by an old Railway `AI_EARLY_WINNER_ENABLED=1` variable.
- Radar 3.0 is the only component allowed to create demand observations; exact remeasurement is owned by Main Bot + View Worker.
- `RadarObservation` now has an expiring cross-replica lease (`lease_owner`, `lease_until`). Due rows are atomically claimed with PostgreSQL `FOR UPDATE SKIP LOCKED`, preventing two Parser replicas from refreshing/writing the same observation batch.
- Failed/unchanged refreshes release their claim for a clean retry; successful measurements release the lease while scheduling the next checkpoint.
- The former AI Lab summary now reports Radar 3.0 baseline/observed/persistent counts instead of stale +1/+3/+6 Early Winner state.
