# v4.23.22 — HOT and popular products on the first screen

Apply over v4.23.21. The `DT Radar 3.0` entry now immediately reads the current
HOT and popular/rising summary plus product names from `radar_products`.

If no dedicated worker snapshot exists, the screen stays useful and does not
appear empty. The main parser prepares the full public/deep snapshot in the
background when `RADAR_DEDICATED_WORKER` is not enabled.

See `RELEASE_4_23_22.md` and `RAILWAY_RADAR_WORKER_RU.txt`.
