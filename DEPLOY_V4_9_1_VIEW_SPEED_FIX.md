# DT PARSER v4.9.1 — View Speed Fix

This is a narrow performance patch on top of v4.9.0. Free Trial, queue, Date Worker,
Page Worker, category/date collection and ranking semantics are unchanged.

## Why
Production logs showed a 149-URL view job being admitted as one Redis job and taking
112.2 seconds at 1.33/s after transient refusals pushed several URLs into Chromium.
Healthy official-counter rounds in the same log still reached roughly 8–15 views/s.

## Changes

1. **Large view batches are always sharded**
   - >=40 URLs always enter the sharded Redis path.
   - default target remains 18 URLs/shard, max 8 shards, expected fleet 4 workers.
   - a transient heartbeat/status read can no longer collapse a large job to one worker.
   - stale Railway sharding variables are ignored unless
     `DT_ALLOW_LEGACY_VIEW_SHARD_TUNING=1` is explicitly set.

2. **One exact HTTP retry before Chromium for 403/429**
   - only explicit 403/429 misses are retried once.
   - success is accepted only from the same official explicit counter payload.
   - unparsed/not-found counters still use the verified browser fallback.
   - no guessed/stale view number is accepted.

3. **Two browser fallbacks fleet-wide**
   - each View Worker replica still runs only one Chromium fallback at a time.
   - the shared Redis browser limit is raised from 1 to 2, so two different replicas
     can recover slow misses concurrently.
   - official HTTP fleet limit remains 16.

4. **Fresh view runtime**
   - default Redis prefix moves to `dtparser:viewcounter:runtime:v491` so old pending
     view jobs from previous releases cannot pollute the new deployment.

## Railway
Redeploy **parser** and **all View Worker replicas** from the same v4.9.1 commit.
Date Worker and Page Worker code is unchanged, but deploying the same commit everywhere
is recommended for version consistency.

No new required variables. Keep 4 View Worker replicas.

Expected bot log for a 149-URL batch:

```text
Remote view sharding parent=... total=149 workers=4 shards=8 target_size=18
Remote view batch queued ... total=19 shard=1/8
...
```

A View Worker should no longer log a newly admitted foreground job with `total=149`;
it should see shard-sized jobs instead.

When transient refusals occur, expect:

```text
Accurate views refusal retry candidates=... recovered=... browser_remaining=...
```

## Smoke test
1. Start one 25-page scan that returns 100+ listings.
2. Confirm `Remote view sharding ... shards=8` in parser logs.
3. Confirm individual View Worker jobs are shard-sized, not the full batch.
4. Run 4 user scans together and watch View Manager: 4 replicas online.
5. Compare total view phase time against v4.9.0.
