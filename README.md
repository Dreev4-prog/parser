# v4.3.22 — STREAMING PAGE WORKER

This release fixes the `0/15`, `0/25`, `0/50` startup pause from v4.3.21.

Page jobs are now dispatched to Redis immediately and the stable foreground collector starts at once. Both Page Worker replicas continue warming upcoming pages in parallel. The foreground path waits only briefly for the next already-owned remote page and otherwise falls back locally. Local fallback results are also published to the shared 180-second cache.

No new Railway variables are required.

See `DEPLOY_V4_3_22_STREAMING_PAGE_WORKER.md`.
