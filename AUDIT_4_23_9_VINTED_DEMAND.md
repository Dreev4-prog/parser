# v4.23.9 Vinted Radar Demand Audit

## Production symptom reviewed

The observed Vinted Radar population had a very large baseline, only a small number of HOT/RISING signals, and thousands of Deals. A substantial part of the baseline also consisted of 2–3 EUR listings that are not useful for the DT product-search use case.

## Findings

### A. Low-price junk was fully admitted

There was no Radar-side minimum price. Low-price items therefore participated in baseline counts, scarcity, seller counts and price medians. Extremely cheap rows could also obtain a maximum Price Edge contribution.

Fix: hard default Radar floor of 40 EUR, enforced both before CPU scoring and in the SQL snapshot query.

### B. Like Velocity used round-start timestamps

The score query selected `VintedScan.created_at` as the timestamp for every item in a round. Full-market Vinted scans contain many segments and can take significant time. The real catalog observation happens when each page is persisted.

Fix: use `VintedScanItem.created_at` for every sample and for the seven-day history cutoff/order.

### C. Expired learning rows contaminated current peers

The first pass correctly separated current Live ids from 7-day learning history, but the peer-reference loop then iterated all primitives. Historical rows could therefore alter current likes/velocity percentiles and brand momentum.

Fix: peer references are built from `live_ids` only.

### D. Age buckets were based on sample span, not current observed age

A product first seen many hours ago but last sampled shortly afterwards could remain in a young peer bucket.

Fix: cohort bucket uses current DT-observed age.

### E. Deal signal was too permissive

Four price peers were enough to establish a median, and price discount plus weak structural bonuses could create a large Deal pool without meaningful demand.

Fix: minimum useful cohort is 8 peers; Deal additionally requires movement or visible above-median interest. Unknown catalog ids cannot share one synthetic price market.

## What was intentionally NOT changed

- HOT threshold 75;
- Rising threshold 58;
- positive-delta requirement for HOT/RISING;
- 24-hour Live window;
- seven-day learning history;
- 60-minute Radar cadence;
- 15 pages per balanced market segment;
- catalog likes remain the Vinted demand signal;
- no fake zeros for UNKNOWN metrics.

The release fixes evidence quality first. If the new funnel shows that most eligible items still receive only one observation, the bottleneck is sampling overlap/cadence and should be solved with a follow-up lane, not by weakening HOT/RISING.

## Validation

- recursive Python compile: PASS;
- release smoke: PASS;
- runtime global-symbol audit: PASS;
- pytest: 267 passed + 167 subtests passed;
- 0 failed.
