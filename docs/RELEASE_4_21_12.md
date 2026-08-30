# DT Parser 4.21.12 — Radar 3.2 Frozen Cohort Full Audit Fix

## Fixes from 4.21.11 audit
1. Replaced order-dependent per-row percentile calculation with a two-pass frozen category cohort.
2. Quiet/zero-growth rows now remain in quantile statistics; <3/h is only the per-listing noise floor.
3. Quantile ties no longer fail a redundant percentile check.
4. Bootstrap categories (<20 observations) can still classify strong listings using conservative fallback gates.
5. Auto and other non-priority market sections are blocked in both AutoScan and user-scan Radar ingestion through one shared category policy.
6. Family confirmation uses already-classified scored/strong evidence, so bootstrap categories are not silently excluded.
7. A RadarProduct is retired immediately when no live Early/Strong observation remains, preventing stale cards from surviving for hours.
8. Analytics counts actual Radar statuses so bootstrap Candidates/Strong signals appear correctly; quiet counts are TTL-bounded.
9. Old 15/30/60 contract tests were retired/replaced with adaptive-model contracts.
10. New reset marker v6 isolates 4.21.12 evidence from earlier scoring semantics.

Raw Listing/ViewHistory remains preserved.
