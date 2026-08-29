# DT Parser 4.21.4 — Radar 3.0 Observation Integrity

This release fixes the full post-audit Radar 3.0 observation path.

## Runtime correctness
- Radar 3.0 checkpoints use a dedicated `radar_checkpoint` traffic priority.
- Generic background work still pauses during AutoScan, while one throttled Radar checkpoint lane may continue so +60m observations do not drift by hours.
- User foreground scans remain absolute priority.
- Radar 3.0 has its own in-process view-refresh lock and cannot be trapped behind a paused legacy background refresh.
- Claim batches stay cross-replica safe with PostgreSQL `FOR UPDATE SKIP LOCKED` leases.

## TTL / stale safety
- Due and claim queries exclude observations whose `expires_at` has passed.
- A refreshed measurement after `expires_at` is marked `expired` and cannot publish a signal.
- Active expired observations are swept independently every observation scheduler cycle.
- Stale Radar products are also expired independently of whether the due queue is empty.

## Startup safety
- Radar 3.0 one-time reset is serialized across Parser replicas with a PostgreSQL transaction advisory lock.
- Raw Listing/ViewHistory remains preserved; only Radar output/observation state is reset by the clean-break marker.

## Legacy AI containment
- `AI_EARLY_WINNER_ENABLED=0` in the example environment.
- Existing Railway AI Worker service remains compatibility-only and is forced disabled; it cannot resurrect legacy +1/+3/+6 writes.

## UI
- AutoScan now reports created Radar 3.0 baselines instead of pretending the initial scan produced Early/Strong/Hot signals.
- The initial counter remains baseline-only and contributes zero points.
