# 4.21.14 Startup Guard Fix

- Defines `RADAR_AUTOSCAN_CATEGORY_TIMEOUT_SECONDS` (default 480s) and `RADAR_AUTOSCAN_VIEW_RECOVERY_TIMEOUT_SECONDS` (default 240s).
- Invalid Railway overrides fall back safely instead of crashing startup.
- `prepare_radar_v3_once()` is permanently non-destructive in normal startup/maintenance. A missing reset marker is repaired without deleting Radar tables.
- Adds an AST contract proving required watchdog globals are assigned, not merely referenced.
- Keeps Radar 3.2 Live/History separation from 4.21.13.
- The 4.21.13 crash happened after its destructive reset; exact deleted RadarProduct scores cannot be reconstructed one-for-one from raw history. Listing/ViewHistory remain intact and live Radar can rebuild from new checkpoints.
