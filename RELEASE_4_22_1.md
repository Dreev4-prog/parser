# DT PARSER 4.22.1 — Vinted Probe: HTML Detail + Stable Pagination

**Base:** 4.22.0 Vinted Parser Probe.

The first live Railway probe proved that anonymous catalog access works, but exposed two concrete incompatibilities with current Vinted:

1. the legacy anonymous JSON item endpoint `/api/v2/items/{id}` returns 404 for every sampled current item;
2. concurrent newest-first pages without a shared pagination snapshot can overlap heavily (the first live run returned 288 rows but only 192 unique items).

This diagnostic release fixes both probe paths without touching Kleinanzeigen.

## Changes

- item metrics now come from the public Vinted item HTML / Next.js hydration payload instead of trusting the legacy anonymous JSON detail endpoint;
- requested item identity remains mandatory before views/favourites/date are accepted;
- missing metrics remain `UNKNOWN`, never synthetic zero;
- page 1 captures `pagination.time` and pages 2+ reuse the same snapshot value;
- the report now records snapshot times and exact page-to-page overlap;
- latency now measures the actual public detail-page path;
- repeated-read stability still gates Radar so we can detect whether reading the public page changes the view counter.

## Important

This is still a probe, not a production Vinted Radar. If repeated public item-page reads change the view count, exact views will remain blocked and we will move to a non-contaminating metric path before Radar is enabled.

No new Railway variables. `Vinted Probe` remains fully isolated from Kleinanzeigen Page/Date/View/AI/Lifecycle queues.
