# v4.23.16 — AutoScan-only Radar baselines

Apply over v4.23.15 and redeploy Parser / Bot. User scans remain fully functional,
but cannot add or re-arm shared Radar observations. AutoScan remains the sole Radar
baseline source and is not given a new admission limit. No SQL or reset is required.
See `RELEASE_4_23_16.md`.
