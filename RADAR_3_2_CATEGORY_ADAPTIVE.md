# DT Radar 3.2 — Category Adaptive Demand (4.21.12)

Radar evaluates live DT-owned post-baseline growth against the listing's own category.

- Noise: <3 views/hour.
- Mature cohort (>=20 fresh measured intervals): P90 Candidate, P95 Early/Score, P98 Strong, P99 Hot interval.
- Bootstrap cohort (<20): conservative 8/15/30/60 views/hour until enough category data exists.
- One frozen category cohort is built before classification, preventing order-dependent decisions inside a refreshed batch.
- Quiet and zero-growth measurements stay in the category distribution to prevent survivor bias.
- Integer ties at quantile cutoffs qualify by the quantile value itself; percentile is used for ranking/score, not as a second rejection gate.
- First scored checkpoint is capped at 50/100.
- Hot: persistent P99/Strong path or strong/persistent demand confirmed by another independently scored listing in the same product family.
- Auto/Immobilien/Jobs/Dienstleistungen/Unterricht/Nachbarschaftshilfe are excluded from all Radar baseline ingestion.
