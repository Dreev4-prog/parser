# DT Parser 4.21.9 — Radar AutoScan Control Recovery

## Fixes

- AutoScan controls are rendered immediately on the DT Radar 3.1 loading screen.
- Start, Stop and Refresh remain usable even when Radar statistics are slow or PostgreSQL is under contention.
- Radar statistics use a shielded single in-flight snapshot so timeout handling no longer waits for asyncpg query cancellation.
- Full state-aware AutoScan controls replace the loading controls as soon as the dashboard snapshot is ready.
- Existing 20-page Today-only AutoScan and Radar 3.1 scoring logic are unchanged.
