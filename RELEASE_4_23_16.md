# DT Parser v4.23.16 — AutoScan-only Radar baseline protection

Base: v4.23.15 Reliability Baseline.

## What changed

- AutoScan is now the only component allowed to create or re-arm shared
  `RadarObservation` baselines.
- Completed user scans no longer enqueue Radar checkpoint work.
- The old user-scan seeding API is retained as a compatibility no-op, so a stale
  caller cannot write to the shared Radar queue.
- User scans still save listings and exact counters, generate exports and keep their
  separate opt-in +3/+6/+12h observation feature.

## What did not change

- AutoScan depth, category coverage and baseline admission have no new limit.
- Candidate, Early, Strong, Score and Hot calculations are unchanged.
- Existing Radar observations, checkpoint history, PostgreSQL data and Redis state
  are preserved.
- No database migration or new Railway variable is required.

## Deployment

Redeploy Parser / Bot from the v4.23.16 checkout. Existing worker services may keep
running during the rollout; keeping the fleet on one commit is recommended.
