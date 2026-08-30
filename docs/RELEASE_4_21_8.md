# DT Parser 4.21.8 — Radar 3.1 Full Audit Fix

This release is a full runtime audit/hardening pass over 4.21.7.

- Non-blocking DT Radar admin entry with immediate Telegram acknowledgement/loading screen.
- Correct RadarProduct status field in dashboard SQL.
- Correct observation expiration UPDATE.
- Full Radar 3.1 evidence reset on observation rearm.
- Active-only category/family peer sets.
- Six-hour stale snapshot guard to prevent old Hot/Strong resurrection.
- Explicit PostgreSQL indexes for additive Radar 3.1 evidence columns.
- Reduced dashboard database round-trips.
- New one-time Radar reset marker because 4.21.7 observations could carry contaminated rearm state.
