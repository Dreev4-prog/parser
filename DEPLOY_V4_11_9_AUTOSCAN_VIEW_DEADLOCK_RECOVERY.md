# DT PARSER v4.11.9 — AutoScan View Deadlock Recovery

Base: v4.11.8.

## Root cause
In stable single-service mode `TRAFFIC.background_during_scans` is forced to `0`. AutoScan wraps an entire category in `TRAFFIC.scan_job_started()`, including the deferred exact-view phase. v4.11.8 asked for those AutoScan counters with `traffic_priority="background"`. Therefore, after the last category page completed, every counter request waited for a background lease while the same AutoScan job kept `scan_jobs_active > 0`. The first counter request could never start.

The Railway signature is: all page logs finish (for example `page=15 relation=target`) and then no `Radar AutoScan views ...` and no `category finish` lines.

## Fix
- AutoScan exact counters switch to a bounded `normal` traffic lane only when background views are disabled by stable mode.
- Lane size is capped at 4 concurrent direct requests.
- Existing global traffic pool, scan reservation, FIFO queue, and user scan protection are unchanged.
- The verified browser fallback uses the same safe priority and remains capped at 24 unresolved URLs.
- Added an explicit `Radar AutoScan views start` log line.

## Expected logs
After the final collected page you should see: 

```text
Radar AutoScan views start category=Autos total=... priority=normal concurrency=4 scan_jobs_active_safe_mode=True
Radar AutoScan views direct category=Autos checked=50/...
Radar AutoScan views complete category=Autos ...
DT Radar AutoScan category finish ... complete=True
DT Radar AutoScan category start ... index=2/84 ...
```

## Deploy
Redeploy **parser service only**. No DB migration. No new Railway variables.
