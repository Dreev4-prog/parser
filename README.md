# DT PARSER v4.8.1 — Golden Core

v4.8.1 is the controlled baseline release: the complete Date/Page/View/traffic parsing core is byte-for-byte v4.4.0, while the modern DT PARSER interface and product features remain.

The goal is to distinguish parser-core regressions from Railway/Redis/runtime/Kleinanzeigen behavior without changing the proven v4.4.0 scan algorithms.

See `DEPLOY_V4_8_1_GOLDEN_CORE.md` for the exact core file list and test procedure.


## v4.8.2 Resilient Traffic
The Golden Core parser remains intact. Date/Page/View now use bounded local refusal handling: no shared 403 freeze, max penalty level 1, no hard pause for 403, and a maximum 3-second local pause for 429. See `DEPLOY_V4_8_2_RESILIENT_TRAFFIC.md`.
