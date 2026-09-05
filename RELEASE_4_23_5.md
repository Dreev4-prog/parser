# DT Parser v4.23.5 — Vinted Lab Instant Navigation

This release removes the remaining small pauses while moving between screens inside **Vinted Lab** after the v4.23.4 global-freeze fix.

## What was still slow

v4.23.4 removed the heavy seven-day Radar rebuild from the Telegram callback path, but several small synchronous network/database reads still happened on almost every Vinted Lab button:

- Redis worker status was read again for each screen and could wait up to two seconds.
- Radar/scan progress re-read PostgreSQL for every Radar filter/page click.
- the full Vinted catalog tree was flattened again while navigating manual-category screens.
- opening a Radar product could still synchronously rebuild an expired Radar snapshot through `get_radar_entry()`.
- Radar result browsing still ran `COUNT(*)` and a `view_count` sort over a potentially huge catalog-only scan table.
- Radar configuration was reloaded from PostgreSQL repeatedly for lightweight UI status reads.

## v4.23.5 changes

- Worker status uses **stale-while-revalidate** with a 2-second freshness window and a **0.75-second hard Redis timeout**. Once a value exists, repeated navigation returns it immediately and refreshes Redis in the background.
- Scan/Radar progress uses the same **stale-while-revalidate** pattern with a 3-second freshness window. Radar filter/page navigation never waits for a cold progress read; it schedules the refresh in the background.
- Vinted scan history gets the same short stale-while-revalidate UI cache.
- The Vinted category tree + flattened index is reused for **30 minutes** instead of being rebuilt while moving through category levels.
- Radar product cards read the **last completed in-memory snapshot/index only**. If the snapshot is stale, refresh starts in the background; the product click never waits for seven-day scoring.
- Radar snapshot entries now have an O(1) item-id index.
- Radar screen no longer makes a second `get_vinted_scan()` DB request after progress has already loaded the scan.
- Radar config has a tiny **3-second memory cache**, invalidated immediately on enable/disable/update.
- Full-market Radar result browsing uses the persisted atomic `scan.total_items` counter and ID order, avoiding `COUNT(*)` + useless `view_count` sorting on each click.

## Expected behavior

Vinted Lab navigation should now feel close to ordinary Telegram menu navigation. A small Telegram network/edit delay can still exist, but Redis/PostgreSQL/Radar workload should no longer be on the critical path of repeated button presses.

## Unchanged

- Vinted Radar scoring formula and thresholds.
- Balanced market segmentation from v4.23.3.
- 15 pages maximum per Radar segment.
- 24h Live / 7-day learning windows.
- Vinted Scan Worker, Metrics Worker and Session flow.
- Kleinanzeigen parser/Radar/workers.

## Deploy

Apply on top of **v4.23.4** and redeploy:

- Parser / Bot
- all Vinted Scan Worker replicas
- all Vinted Metrics Worker replicas

No SQL migration and no new Railway variables are required. Vinted Session Worker does not need a functional change.
