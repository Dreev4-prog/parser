# DT Parser v4.3.32 — SMART HYBRID OLD DATE

- Fixes v4.3.31 where a date deeper than the nationwide public window could finish in seconds with a false zero.
- Normal reachable dates stay nationwide-only.
- Verified `too_deep` dates automatically fall back to regional shards for correctness.
- `15/25/50` remains a maximum nationwide depth when the date is reachable nationwide.
- No Railway variable changes required.

## v4.3.31 — NATIONWIDE MAX DEPTH

This test release removes the regional hidden-fill from the default scan path.

- `15 / 25 / 50` now means **maximum nationwide target-date pages**, not a mandatory depth that must be filled from regional feeds.
- After the target date is found in the Germany-wide feed, the bot scans forward until one of three things happens: the selected date ends, the chosen max depth is reached, or Kleinanzeigen's public nationwide page window ends.
- If the selected date itself is deeper than the public nationwide window, the bot reports that limitation explicitly instead of starting a multi-minute regional crawl.
- Regional hidden-fill code is preserved as an opt-in rollback path: `REGIONAL_HIDDEN_FILL_ENABLED=1`. Default is OFF.
- Date Worker / Predictor / Cold Date Turbo / Page Worker / View Worker remain unchanged.


## v4.3.30 — REGIONAL SHARD FIX

Fixes the Cold Date Turbo regional recursion regression from v4.3.28/v4.3.29.
When a regional feed is proven deeper than page 50, the main stable parser now verifies page 1 first so Kleinanzeigen child-location shards are discovered before returning `too_deep`. The remote Date Worker remains a hint only and all page/date truth is still locally verified. Page Worker and View Worker logic are unchanged.
# DT PARSER v4.3.29 — COLD DATE TURBO + PARALLEL HIDDEN FILL

v4.3.29 targets the slowest remaining date scenario: the **first cold scan of an old date** (especially day 5–6 of the allowed 7-day window) and the second regional date-search phase that can appear after nationwide pages are exhausted.

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

## v4.3.33 — Parallel Regional Locator + Regional/Page Pipeline

For old dates that are deeper than the nationwide 50-page window, regional fallback now pipelines work instead of waiting for every region serially:

- Date Worker pre-locates upcoming regional feeds in a rolling window while the current region is being verified/collected.
- Ready regional hints are consumed first, reducing foreground idle time.
- A precomputed remote hint is reused instead of launching the same Date Worker search twice.
- Every remote hint remains acceleration-only: the foreground stable parser still verifies the candidate boundary before any page is accepted.
- Page Worker/cache continues to prefetch the verified region while Date Worker works on the next regions.
- Regional pipeline timing is logged (`locator_wait`, `collect`) for real bottleneck measurement.

No new Railway variables are required. Defaults are conservative: 4 queued regional hints, at most 2 regional locator jobs running concurrently. Optional rollback: `REGIONAL_DATE_PIPELINE_ENABLED=0`.
