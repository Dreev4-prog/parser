# DT Parser v4.23.10 — Vinted Radar Follow-up Lane

## Why

Vinted is a fast newest-first market. A product found on page 1-15 can move much deeper before the next 60-minute full-market pass. In v4.23.9 this meant a strong item could remain a one-sample baseline even when its likes were exploding. Lowering HOT/RISING thresholds would create false positives, so v4.23.10 fixes sampling coverage instead.

## Architecture

The Radar is now two independent lanes:

1. **Full Market Discovery** — unchanged 60-minute balanced-market scan, 15 pages per segment, >=40 EUR floor.
2. **Targeted Follow-up** — a bounded promising subset gets identity-bound favourite-count measurements at +30 / +60 / +120 / +180 minutes through the existing Vinted Metrics Worker browser-session endpoint.

The follow-up lane does not navigate item pages and does not use fake/approximate counters. Protected/UNKNOWN responses remain UNKNOWN and are retried once by default.

## Discovery gate

A lightweight discovery score chooses where to spend follow-up capacity. It is **not** the public Vinted Score 100 and cannot itself create HOT/RISING. It combines:

- current likes and catalog-relative like percentile;
- price edge against a useful >=8-peer catalog cohort;
- brand scarcity;
- seller footprint;
- freshness.

Defaults cap admission at 1500 new watches/hour and 4500 active watches so the Metrics fleet is not flooded.

## Like Momentum integration

Only identity-verified follow-up `favourite_count` values are stored as `VintedMetricHistory.source = radar_followup:*` and merged into Radar Like Momentum. Catalog and follow-up observations closer than 10 minutes are coalesced, preventing a near-duplicate +60 minute reading from hiding the real +30 -> +60 growth interval.

The user-facing rules stay strict:

- first observation is baseline-only;
- HOT >=75 and Rising >=58 are unchanged;
- HOT/RISING still require confirmed positive like movement;
- 24h Live / 7d learning remain unchanged;
- >=40 EUR floor remains unchanged.

## Stop behavior

`Остановить Radar AutoScan` stops new full-market rounds and therefore stops **new watch discovery**. Watches that were already selected continue their short +30/+60/+120/+180 schedule so existing evidence is not thrown away. After the last due checkpoint, background follow-up naturally drains to zero.

## Durability / crash recovery

New additive table `vinted_radar_watches` stores watch state, current step, next due time, retry state and a bounded lease. Redis messages are idempotent by watch+step. A Parser/worker restart releases stale leases automatically instead of duplicating evidence.

## UI

Vinted Radar shows:

- under observation;
- due now;
- targeted follow-up samples;
- the existing one-sample -> repeated -> positive-growth funnel.

## Optional Railway tuning

No variable is required. Defaults are production-safe. Optional overrides:

- `VINTED_RADAR_FOLLOWUP_ENABLED=1`
- `VINTED_RADAR_FOLLOWUP_DISCOVERY_LOOKBACK_MINUTES=90`
- `VINTED_RADAR_FOLLOWUP_MIN_DISCOVERY_SCORE=42`
- `VINTED_RADAR_FOLLOWUP_MAX_NEW_PER_HOUR=1500`
- `VINTED_RADAR_FOLLOWUP_MAX_ACTIVE=4500`
- `VINTED_RADAR_FOLLOWUP_SWEEP_LIMIT=300`
- `VINTED_RADAR_FOLLOWUP_DISPATCH_BATCH=100`
- `VINTED_RADAR_FOLLOWUP_SEED_INTERVAL_SECONDS=120`
- `VINTED_RADAR_FOLLOWUP_RETRY_MINUTES=10`
- `VINTED_RADAR_FOLLOWUP_RETRIES_PER_STEP=1`

## Deployment

Apply on v4.23.9. Redeploy Parser/Bot and all Vinted Metrics Worker replicas. Same-commit Vinted Scan Worker redeploy is recommended; Session Worker and Kleinanzeigen workers are functionally unchanged. `init_db()` creates the new table automatically.
