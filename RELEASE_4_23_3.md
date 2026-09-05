# DT Parser v4.23.3 — Vinted Radar Balanced Market Segments

Vinted Radar 1.0 no longer schedules every terminal Vinted catalog id as a separate 15-page task.

## What changed

- The complete DE category tree is still validated through all terminal leaves.
- Radar then partitions the tree into a bounded, non-overlapping mixed-depth market plan.
- Target: about **120 Radar segments**; hard cap: **150**.
- Every segment receives the same **maximum 15-page** primary depth.
- A selected parent is replaced by its children during refinement, so parent and child are never scanned together.
- Full-market coverage is preserved while the request plan drops from potentially ~36,000 pages to roughly 1,800 pages maximum around the 120-segment target.
- The start/progress UI now says `Radar segments` and shows the real segment/page passage.
- Existing v4.23.1/v4.23.2 `all_leaf_categories` configuration migrates automatically. A legacy active leaf scan is cancelled safely and a new optimized round starts without waiting for the old one-hour interval.
- Queued scan categories are marked `cancelled` immediately on stop so a large queued scan cannot remain stuck forever.
- When catalog search exposes an item's own `catalog_id`, Radar stores that precise category id for peer scoring; otherwise it safely falls back to the scan segment id.

## Unchanged

- Like Momentum / acceleration / price edge / scarcity / seller / brand score.
- 24h Live window and 7-day learning window.
- 15-page Radar ceiling.
- Manual Vinted Parser behaviour and Metrics Worker flow.
- Kleinanzeigen parser, Radar and all Kleinanzeigen workers.
