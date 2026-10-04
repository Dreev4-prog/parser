# v4.23.21 — complete Radar 3.0 first screen

Apply over v4.23.20. Create exactly one Railway service with role
`radar-worker`, then set `RADAR_DEDICATED_WORKER=1` on the main parser service.

The worker owns AutoScan, checkpoint measurements, Radar maintenance and durable
public/deep analytics snapshots. Telegram reads those saved snapshots instantly;
it does not calculate analytics in a callback. Opening `DT Radar 3.0` now goes
straight to the complete saved analytics screen; Live progress is secondary.

See `RELEASE_4_23_21.md` and `RAILWAY_RADAR_WORKER_RU.txt`.
