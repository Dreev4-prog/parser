# v4.23.25 — DT Radar seven-day statistics window

Radar keeps the v4.23.17-style workflow from 4.23.24, but user-facing history
aggregates no longer rescan months of accumulated PostgreSQL rows.

- Signals, Fast Sold and Noise/Weak use the last 7 days.
- Fast Sold feeds, normal search and category counters use the same week window.
- HOT/Rising remain stricter: current evidence only (6–48 hours).
- `🏆 Рекорды Radar`, favorites and product history remain all-time.
- No rows are deleted and no database migration is required.

See `RELEASE_4_23_25.md`.
