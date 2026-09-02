# DT Radar 3.2 — Category Adaptive / Two-Pass

Radar compares each listing with fresh DT-observed demand in its own leaf category.

- Noise: <3 views/hour.
- Candidate: category P90.
- Early / Score: category P95.
- Strong: category P98.
- Hot interval: category P99; final Hot requires persistence or independent product-family confirmation.
- Minimum cohort safeguard: until 20 usable peers exist, conservative fallback gates are used.
- Two-pass evaluation: a refreshed batch is fully persisted before category thresholds are calculated, so processing order inside the batch cannot change the result.
- Auto/real-estate/jobs/services/courses/neighborhood-help are excluded from Radar from every baseline source.
- Release 4.21.14 performs a new one-time Radar reset; raw listing/view history remains intact.
