# DT Parser 4.23.22 — Radar database home restored

`DT Radar 3.0` no longer depends on the existence of a worker-produced snapshot
to show useful data. The first screen performs one bounded lightweight read of
`radar_products` and displays current HOT and popular/rising counts together
with the strongest product names.

The fallback deliberately avoids the expensive listing-visibility, lifecycle
and snapshot-history joins. Exact full counters still come from the persisted
worker snapshot when it is available.

When `RADAR_DEDICATED_WORKER` is disabled, the main parser now refreshes the
complete public and Adaptive Analytics snapshots in the background while both
user scans and Radar AutoScan are idle. This keeps the existing one-service
Railway installation functional. A dedicated Radar Worker remains the preferred
setup for isolating Radar resource use.

The worker-process marker is now separate from the main-service ownership flag.
`RADAR_DEDICATED_WORKER=1` on the main parser only disables duplicate Radar
background loops and no longer changes or breaks its four user-scan lanes. Only
the `radar-worker` process receives `RADAR_WORKER_PROCESS=1` automatically.

No database migration is required. Radar scoring, saved products and scan
admission rules are unchanged.
