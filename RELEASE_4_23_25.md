# DT Parser 4.23.25 — seven-day Radar statistics window

This release keeps the restored v4.23.17 Radar UI and main-parser ownership, but
bounds the expensive historical queries that made DT Radar increasingly slow as
PostgreSQL accumulated months of data.

The following user-facing aggregates now use the last seven days:

- Radar signal count (`radar_snapshots.recorded_at`);
- Fast Sold count and feed (`radar_lifecycle_watches.disappeared_at`);
- Noise/Weak observations (`radar_observations.updated_at`);
- normal product search and category counters (`radar_products.last_signal_at`).

The opening screen skips signal-history queries that it does not display.
Lifecycle diagnostics group only active watching/confirming rows instead of the
entire historical lifecycle table.

HOT and Rising remain based on the existing stricter fresh-evidence windows of
6–48 hours. `🏆 Рекорды Radar`, favorites and individual product history
remain all-time. The patch does not delete any row, change scoring or require a
database migration.
