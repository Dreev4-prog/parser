# DT PARSER v4.3.28 — COLD DATE TURBO + PARALLEL HIDDEN FILL

v4.3.28 targets the slowest remaining date scenario: the **first cold scan of an old date** (especially day 5–6 of the allowed 7-day window) and the second regional date-search phase that can appear after nationwide pages are exhausted.

## What changed

- **Cold Date Turbo**: first-ever date searches use one age-aware broad probe grid instead of the old dependent `1/2/4/8/16/32/50` ladder.
- With the recommended **Date Worker ×2**, each replica now defaults to **4 HTTP-first consumers**: up to 8 cheap date probes can be processed in parallel.
- Browser confirmation remains separately throttled to **1 per replica** by default, so HTTP parallelism does not turn into a Chromium storm.
- If a locally revalidated public page 50 is still newer than the target date, the bot jumps directly to the regional sharder instead of repeating the full local date ladder.
- **Parallel regional prewarm**: when a 50-page historical scan is likely to need regional depth, Date Worker starts warming several regional date boundaries while nationwide Page Worker collection is still running.
- Hidden/regional feeds keep a rolling prewarm window, so later regions are discovered in parallel instead of one long sequential date-search chain.
- Telegram now labels that phase as **Региональный добор даты** instead of making it look like the original date search restarted.
- Predictor/Continue Search from v4.3.26–v4.3.27 stays enabled for repeat scans.

## Accuracy / safety

- Remote Date Worker results are still **hints only**.
- The foreground stable parser still locally verifies the final date boundary.
- The new `beyond page 50` shortcut is accepted only after local verification that page 50 is still newer than the selected date.
- Weak HTTP probes still require browser confirmation.
- 403/429 handling remains unchanged.
- `parser.py`, `traffic.py`, Page Worker and View Worker core are unchanged.

## Railway

No new mandatory Railway variables or services are required. Keep the existing **Date Worker** service at **2 replicas**.

Optional rollback/tuning variables:

```env
DATE_COLD_TURBO_ENABLED=1
DATE_WORKER_CONCURRENCY=4
DATE_WORKER_BROWSER_CONFIRM_CONCURRENCY=1
HIDDEN_DATE_PREWARM_ENABLED=1
HIDDEN_DATE_PREWARM_WINDOW=6
HIDDEN_DATE_PREWARM_CONCURRENCY=4
```

The defaults above are already built in; you do not need to add them unless you want to tune or disable a feature.

See `DEPLOY_V4_3_28_COLD_DATE_TURBO.md`.
