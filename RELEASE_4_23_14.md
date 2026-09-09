# DT Parser v4.23.14 — Radar Checkpoint Runtime Fix

Base: exact v4.23.13.

This release repairs three production-only Radar inconsistencies found in Railway telemetry:

- The checkpoint dashboard aggregate now returns an integer flag on both PostgreSQL `CASE` branches. This removes the `CASE types integer and boolean cannot be matched` error that made the saved checkpoint statistics appear as zeros.
- Claimed observations that can never produce a valid exact checkpoint (missing listing or URL, promoted, price-reduced, or dirty identity) are marked excluded once instead of occupying the oldest queue slots on every scheduler pass.
- The admin dashboard Early/Strong/Hot totals and category counts now use the same live, clean, visible, and six-hour freshness rules as the public Radar catalogue.

The scoring formula, percentile thresholds, confirmation requirements, six-hour current-signal window, 48-hour retention, category selection, and scan depth are unchanged. Existing observations, Radar records, products, favorites, database data, and Redis state are preserved. No SQL migration, cleanup, data reset, or new Railway variable is required.

Redeploy Parser / Bot. A running Radar round does not need to be restarted; its observations can continue through the new scheduler logic.
