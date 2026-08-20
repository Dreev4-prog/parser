# DT PARSER v4.3.35 — VIEW FLEET GUARD

Fixes the long tail seen with 3 simultaneous scans and View Worker ×3.

## What the logs proved
Workers independently ramped to pool=10. With three replicas this could create a large aggregate burst, followed by repeated HTTP 403 responses. Accurate Views then entered slow browser fallback and rounds timed out after 180 seconds.

## Changes
- View Worker replicas now enable the existing Redis distributed traffic coordinator.
- Shared fleet-wide view concurrency default: 16.
- A 403/429 on one replica publishes a shared cooldown seen by all three replicas.
- Per-replica adaptive pool: 4 / 5 / 6 instead of 6 / 8 / 10.
- Round size: 24.
- Round/stall timeout: 120 seconds instead of 180.
- Large view batches use 3 shards per healthy worker, target shard size 180, so one slow worker owns a smaller part of a scan.
- Exact view extraction stays unchanged. parser.py is unchanged.

## Railway
Keep View Worker replicas = 3. No new variables are required; these are safe baked-in defaults.
