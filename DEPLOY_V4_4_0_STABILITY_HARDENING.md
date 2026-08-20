# DT Parser v4.4.0 — Stability Hardening

## Railway layout

- Main Bot ×1
- PostgreSQL ×1
- Redis ×1
- Date Worker ×4
- Page Worker ×4
- View Worker ×4

No new mandatory Railway variables are required.

## Fleet guards

Date and Page workers now enable the existing Redis distributed traffic governor deliberately. They use separate per-role buckets (`date`, `page`) plus one shared `search-fleet` global budget. This means four replicas improve distribution/recovery without multiplying the request burst by four.

Production baked-in budgets:

- Date HTTP scan fleet: 4 in-flight
- Date browser-confirm fleet: 2 in-flight
- Page browser fleet: 4 in-flight
- Date + Page search-fleet global: 8 in-flight
- View fleet global: 16 official-counter requests

A shared cooldown is scoped per fleet, so a view-counter 403 does not freeze date/page work, while every replica inside the affected fleet backs off together.

## Rolling Page Worker prefetch

The old streaming implementation could enqueue almost the entire requested 50-page range before chronology proved those pages were needed. v4.4.0 keeps a rolling window (default 10 pages, low-water 4). If the selected date ends early, unused deep pages are never queued.

## View partial recovery

A failed remote shard no longer invalidates healthy completed shards. The main bot merges all exact remote results and invokes the unchanged local exact view path only for missing URLs.

## DB safety

`upsert_page_items()` owns `db_write_lock` internally. This protects its unique `Listing.external_id` SELECT/INSERT path even if a future caller forgets an outer lock.

## Telemetry / configuration

All worker heartbeats use the shared `VERSION` file (`4.4.0`). Critical worker limits are pinned to the stable production profile unless `DT_ALLOW_LEGACY_WORKER_TUNING=1` is deliberately enabled.

## Explicitly unchanged

- Moscow date semantics are unchanged.
- Category hard watchdog remains 1200 seconds.
- Final date truth still belongs to the foreground parser.
- Weak Page Worker cache entries are still rejected.
- Views remain exact/fail-closed.

## Verification

Run:

```bash
python -m compileall .
python -m unittest discover -s tests -v
```

Recommended load test after deploy: four users, each up to two categories, 50 pages, one of the oldest available dates.
