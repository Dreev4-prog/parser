# DT Parser 4.23.21 — complete Radar 3.0 first screen

This release makes the main `DT Radar 3.0` entry open the complete saved
Adaptive Analytics snapshot immediately. There is no intermediate Live-only
screen and no heavy aggregate calculation in the Telegram callback.

The complete first screen includes the Radar funnel, category demand rows,
checkpoint telemetry, Fast Sold diagnostics and the worker snapshot time.
AutoScan controls stay on that screen. Live progress remains available through
the separate `Live-прогресс AutoScan` button.

The dedicated `radar-worker` calculates and stores the snapshot in PostgreSQL.
The Telegram service reads it with a bounded primary-key lookup, so the screen
does not compete with scans for database resources.

No database migration is required. Radar scoring and stored products are
unchanged.
