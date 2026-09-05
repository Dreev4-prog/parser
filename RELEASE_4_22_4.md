# DT PARSER 4.22.4 — Vinted Admin Lab + Isolated Worker Fleet

**Base:** 4.22.3 Vinted Session/OAuth Exact Metrics Probe.

This release turns the Vinted proof-of-concept into an admin-only test surface without touching the production Kleinanzeigen parser path.

## Admin Vinted Lab

- new `🟣 Vinted Lab` entry inside the existing admin panel;
- `🔎 Новый Vinted-скан` and `📡 Тестовый Radar-круг` stay admin-only;
- live Vinted category tree is loaded from catalog initializers with a safe broad-category fallback;
- hierarchical category navigation and multi-selection;
- selectable depth: 1 / 3 / 5 / 10 pages per category;
- live catalog progress percentage and exact-metrics percentage;
- category-by-category status, pages, discovered item count and errors;
- persistent scan history and item result cards;
- result cards preserve `UNKNOWN` when Vinted cannot prove exact views, favourites or chronology;
- best-effort automatic Telegram progress edits plus a durable manual `Refresh` button.

## Isolated Vinted worker fleet

Two new Railway roles are available through the existing `python service_launcher.py` command:

- `Vinted Scan Worker` — catalog-only collection; recommended **2 replicas**;
- `Vinted Metrics Worker` — exact item metrics only; recommended **2 replicas**.

They use the separate Redis namespace `dtparser:vintedlab:*` and dedicated `vinted_*` PostgreSQL tables. Existing Kleinanzeigen Page/Date/View workers never consume these jobs and the Vinted workers never consume Kleinanzeigen jobs.

## Accuracy rules retained

- catalog `view_count=0` is diagnostic only and never becomes an exact zero;
- item ID must match before exact metrics are accepted;
- missing/blocked detail remains `UNKNOWN`;
- raw exact-metric attempts are stored in `vinted_metric_history`;
- the test Radar mode collects a baseline but does not invent Rising/Hot while the Exact Views quality gate is still failing.

## Optional auth

`VINTED_SESSION_JSON` is supported as one optional Railway secret containing cookie data. It is **not required** to run the admin parser/catalog test. Cookie values are never logged by the probe.

## Railway

1. Redeploy **Parser** from this checkout.
2. Create **Vinted Scan Worker**, Start Command `python service_launcher.py`, set replicas to **2**, and attach the same `DATABASE_URL` + `REDIS_URL`.
3. Create **Vinted Metrics Worker**, same Start Command, replicas **2**, same `DATABASE_URL` + `REDIS_URL`.
4. Existing Kleinanzeigen services do not need configuration changes.

Database tables are additive and created by the existing serialized `init_db()` startup path. No manual SQL migration is required.
