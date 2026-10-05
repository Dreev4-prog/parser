# DT Parser 4.23.24 — Radar restored to v4.23.17 behavior

This patch deliberately restores the DT Radar workflow from version 4.23.17
without rolling back unrelated project protections.

- The main Telegram parser again owns Radar AutoScan, exact checkpoint
  measurements, maintenance and organic velocity.
- Opening the admin Radar panel displays the lightweight live AutoScan status.
- The `📊 Аналитика Radar` button calculates the full current
  Candidate/Early/Strong/HOT funnel, category rows, checkpoint coverage and Fast
  Sold diagnostics directly from PostgreSQL, as in 4.23.17.
- The public DT Radar screen and “Best now” screen read current database counters
  rather than worker-produced snapshots.
- The separate Radar Worker and `RADAR_DEDICATED_WORKER` flag are not required.

The PostgreSQL retention scheduler, 15-minute Chromium idle shutdown, disabled
Vinted Lab and other non-Radar fixes remain in place. There is no database
migration and no stored Radar data is deleted.
