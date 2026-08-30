# DT Parser 4.21.11 — Radar 3.1 Live Dashboard

The Radar control plane is split into fast Live Status and isolated Deep Analytics. The live screen never calls `_radar3_dashboard_snapshot()`. AutoScan control remains available even if aggregate PostgreSQL analytics is slow.
