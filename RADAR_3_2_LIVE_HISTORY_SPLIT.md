# DT Radar 3.2 — Live / History Split (4.21.16)

- The DT-owned **observation window remains 6 hours**. This is how long one baseline is actively remeasured.
- A confirmed product can remain in the live catalogue for **up to 24 hours** so Radar does not drain between daily AutoScan passes.
- A successful AutoScan of the same category is an earlier freshness boundary: old live families absent from the freshly verified clean category set move to `historical` immediately.
- Product families still represented in the new successful category set remain live while their fresh observation cycle runs.
- Failed/partial category passes never retire live products.
- `radar_rank` becomes zero for History so stale products cannot compete in live ranking.
- History preserves the last confirmed Score, Peak Score, snapshots and product row; nothing is deleted by live expiry/rollover.
- On first 4.21.16 startup, recent 6–24h products already historicalized by the old TTL are restored from preserved Radar 3.2 snapshots once.
- Hot, Rising, category New/Best, generic live catalogue, category counters and Search exclude `historical` products.
- `Records Radar` remains the explicit historical view and sorts by Peak Score.
- Favorites may keep historical products because they were explicitly saved by the user.
- Startup remains non-destructive; no Radar reset is performed.
