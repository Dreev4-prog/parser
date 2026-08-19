# DT Parser v4.3.33 — Parallel Regional Locator + Regional/Page Pipeline

Built on v4.3.32 Smart Hybrid Old Date.

## What changed

Only the old-date regional fallback orchestration was changed.

1. Upcoming regional date searches are started in advance on Date Worker.
2. At most 2 regional locator jobs run concurrently by default; up to 4 are kept in the rolling queue.
3. If a later region's hint is already ready, it can be processed before an unfinished earlier queue item.
4. The finished Date Worker hint is passed into the foreground locator, so the same remote search is not repeated.
5. The foreground stable parser still verifies the boundary locally. A remote hint never becomes source of truth.
6. While the current region is collected through Page Worker/cache, Date Worker prepares the next region.
7. Logs now include regional `locator_wait` and `collect` seconds.

## Railway

No changes required. Keep the existing two Date Worker replicas and two Page Worker replicas.

Optional rollback only:

```text
REGIONAL_DATE_PIPELINE_ENABLED=0
```

Optional tuning (defaults are already baked in):

```text
REGIONAL_DATE_PIPELINE_WINDOW=4
REGIONAL_DATE_PIPELINE_CONCURRENCY=2
```
