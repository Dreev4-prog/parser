# v4.23.24 — DT Radar restored to the 4.23.17 workflow

This release restores the Radar behavior used in v4.23.17:

- the main parser owns AutoScan, checkpoint measurements and Radar maintenance;
- the Radar admin entry opens the live AutoScan status;
- `📊 Аналитика Radar` calculates the full current funnel directly from PostgreSQL;
- the public Radar menu reads current counters directly from the database;
- no dedicated Radar Worker or saved analytics snapshot is required.

Storage retention, Chromium idle shutdown and unrelated project fixes remain.
No database migration is required. See `RELEASE_4_23_24.md`.
