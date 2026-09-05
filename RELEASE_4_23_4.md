# DT Parser v4.23.4 — Vinted Lab Non-Blocking UI

This release fixes the case where opening **Vinted Lab** could make the whole Telegram bot feel frozen while a large Vinted Radar round/history was active.

## Root causes fixed

- Vinted Lab home rebuilt the complete seven-day Vinted Radar score snapshot on the Telegram callback path whenever the short cache expired.
- Full-market Radar progress repeatedly aggregated likes across a potentially very large `VintedScanItem` table.
- Radar scan recalculation loaded every scan item into Python after category completion.
- Live scan watchers could keep editing the same Telegram message after the admin navigated to another Vinted screen.
- Redis worker-state reads had no UI timeout.

## What changed

- **Vinted Lab home is lightweight:** no full Radar snapshot and no full scan-item likes aggregate on entry.
- Radar snapshot cache default is **120 seconds** and only one refresh can run at a time.
- Home pre-warms a stale/missing Radar snapshot **in the background**.
- Radar UI renders the last completed snapshot immediately; on a cold process it shows `Radar summary is recalculating in background` instead of blocking a Telegram callback.
- The CPU-heavy Like Momentum / percentile / median / score pass is executed with `asyncio.to_thread`, outside the Telegram asyncio event loop.
- Radar history SQL now selects only the scalar columns actually used by scoring instead of hydrating complete ORM item objects.
- Radar progress no longer runs `COUNT/SUM/MAX` over all scan items on every watcher refresh. Page/category progress uses persisted scan counters.
- Radar `recalc_scan()` no longer loads the entire item table; item totals are maintained incrementally and atomically while pages are saved.
- Scan watcher interval is reduced from aggressive 4-second updates to 8 seconds, DB/Redis UI reads are bounded, and watchers are tied to the Telegram message being viewed.
- Navigating away from a scan cancels the old watcher before rendering the next Vinted Lab screen.

## Unchanged

- Vinted Radar 1.0 scoring formula and thresholds.
- Balanced full-market plan from v4.23.3 (~120 non-overlapping segments, hard cap 150).
- 15-page maximum per Radar segment.
- 24h Live window and 7-day learning window.
- Vinted Scan Worker / Metrics Worker responsibilities.
- Kleinanzeigen parser, Radar and Kleinanzeigen workers.

## Deploy

Apply this patch on top of **v4.23.3** and redeploy:

- Parser / Bot
- all Vinted Scan Worker replicas
- all Vinted Metrics Worker replicas

Vinted Session Worker does not require a functional change for this release. No SQL migration and no new Railway variable are required.
