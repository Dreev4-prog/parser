# 4.21.16 Radar 24H Category Handoff

Base: **4.21.15 Radar Equal Home UI**.

## What changed

- The DT-owned **measurement/observation window stays 6 hours**. Radar still stops actively rechecking one baseline after that evidence window.
- A confirmed Radar product now remains in the **live catalogue for up to 24 hours**, instead of being pushed to History after only 6 hours.
- On the first 4.21.16 Parser startup, products that were already moved to History by the old 6-hour rule but still have a confirmed signal from the last 24 hours are restored from their preserved Radar snapshots. Older History is not resurrected.
- A **successful AutoScan of the same category** is now a freshness boundary:
  - product families still represented by a clean listing in the freshly verified category result remain live while the new observation cycle runs;
  - older live families absent from that newly verified category set are moved to History immediately;
  - failed/partial AutoScan categories do **not** retire anything.
- Moving a product to History never deletes the product, snapshots, Peak Score or last confirmed Score. `radar_rank` becomes `0` so History cannot leak back into live ranking.
- The 24-hour hard cap remains the fallback if the next category AutoScan is delayed or its freshness rollover fails.

## Why

The previous 6-hour live TTL caused DT Radar to look full after AutoScan and then drain heavily later in the day, even though the next full market pass had not yet arrived. 4.21.16 separates **how long Radar measures a baseline** from **how long a confirmed result stays visible**.

## Safety / scope

- No database migration.
- No new Railway variables.
- No Page/Date/View parsing changes.
- No change to the Radar 3.2 P90/P95/P98/P99 adaptive thresholds.
- No change to the first-counter-is-baseline-only rule.
- No destructive startup cleanup.

## Deploy

Redeploy the **Parser** service. Other workers are algorithmically unchanged.

## Validation

```bash
python -m compileall -q .
pytest -q
python scripts/release_smoke.py
python scripts/check_runtime_globals.py
```
