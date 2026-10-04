# DT Parser 4.23.20 — dedicated Railway Radar Worker

This release moves Radar background ownership into exactly one Railway service.

The `radar-worker` owns:

- Radar AutoScan;
- baseline checkpoint remeasurement;
- Radar maintenance and verified velocity;
- the complete public Radar counter snapshot;
- the full Adaptive Analytics snapshot, including the funnel, category rows,
  checkpoint telemetry and Fast Sold diagnostics.

The main Telegram parser keeps the controls and product UI, but reads the saved
snapshots with one primary-key query. Opening Radar or Adaptive Analytics never
starts a full-table aggregate.

Set `RADAR_DEDICATED_WORKER=1` on the main parser first and wait for its redeploy.
Then create one Railway service from the same repository with `DT_SERVICE_ROLE=radar-worker`, `DATABASE_URL`,
`REDIS_URL` and `BOT_TOKEN`. Keep its replica count at exactly one.

No database migration is required. Radar scoring, observations, visibility and
saved products are unchanged.
