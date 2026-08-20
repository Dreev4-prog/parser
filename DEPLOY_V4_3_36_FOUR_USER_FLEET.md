# v4.3.36 — FOUR-USER FLEET

Target Railway layout:

- Main Bot ×1
- PostgreSQL ×1
- Redis ×1
- Date Worker ×4
- Page Worker ×4
- View Worker ×4

No new environment variables are required.

## What changed

- Regional old-date pipeline default concurrency: 4 (was 3).
- Regional look-ahead window: 8 (was 6).
- View sharding: 4 shards per healthy worker; four View replicas can spread a large scan over up to 16 small shards.
- Every View Worker owns only one active shard/job at a time by default. A slow replica therefore cannot pin two large pieces of the same scan.
- Fleet-wide official-counter HTTP budget remains 16 across all View Worker replicas. Adding a fourth View Worker does not raise pressure on Kleinanzeigen.
- Shared Redis 403/429 cooldown from v4.3.35 remains enabled.
- Per-replica Date/Page concurrency is unchanged; the fourth replica adds capacity through distribution, not more aggressive settings inside a worker.

## Railway

Open each of these services and set **Replicas = 4**:

1. Date Worker
2. Page Worker
3. View Worker

Redeploy after updating the repository.

## Safety

The trusted parser/date/page verification logic is unchanged. View counts still come from the same exact counter/fallback logic; only scheduling and fleet limits changed.
