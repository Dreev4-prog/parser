# DT PARSER v4.0.1 — Tolerant Chronology Fix

This is a drop-in update for the v4.0 Browser Fleet deployment.

## Why this release exists

v4.0 successfully added real Railway browser capacity, but the page chronology
validator was still too strict. A result page became `unknown` when only part of
the cards exposed parseable publication dates. After bounded retries this could
mark a category partial or fan the scan out into the expensive regional fallback.

v4.0.1 changes chronology validation from percentage-gated to evidence-based:

- an exact target-day timestamp is direct target evidence;
- two trustworthy timestamps on the same side are enough for newer/older direction;
- mixed newer/older timestamps are a boundary signal, not a parser failure;
- low date coverage remains telemetry, but no longer invalidates a useful page;
- isolated weak pages are tolerated and neighboring pages are checked;
- `unknown` chronology no longer triggers regional hidden-fill automatically;
- hidden/regional fallback is reserved for a target date genuinely beyond the
  public nationwide page window.

## Railway layout

Keep the same v4.0 layout:

- bot ×1
- fleet-worker ×6 replicas (or your existing count)
- views-worker ×1
- Redis ×1
- PostgreSQL ×1

Fleet worker start command:

```bash
python fleet_worker.py
```

No PostgreSQL migration is required.

## Recommended variables

The fleet worker already provides these defaults. If the same variables exist in
Railway, use:

```env
FLEET_CONTEXTS_PER_REPLICA=2
FLEET_TOTAL_SCAN_LANES=8
FLEET_TOTAL_GLOBAL_LANES=10
MIN_PAGE_DATE_COVERAGE=0.20
MIN_PAGE_DATED_ITEMS=2
MIN_DIRECTION_DATED_ITEMS=2
STABLE_WEAK_PAGE_GAP_LIMIT=3
STABLE_PAGE_RETRIES=2
```

Do not run parser-worker, browser-worker, hybrid-worker, or stable-worker against
the same Redis scan queue while testing fleet-worker.

## Test plan

1. Redeploy bot and all fleet replicas from the same v4.0.1 commit.
2. Start 5 accounts at nearly the same time.
3. Use one category and 25 pages first.
4. Then repeat with different categories.
5. The normal result should no longer become partial merely because one page has
   low timestamp coverage.

If a scan still becomes partial, inspect fleet logs for actual transport evidence:
HTTP 403/429, challenge/access page, repeated page, redirect to the wrong page, or
several consecutive pages with zero parseable timestamps. Those are now separated
from harmless low date coverage.
