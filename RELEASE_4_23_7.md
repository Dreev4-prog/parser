# DT PARSER v4.23.7 — Full Audit Hardening

**Base:** v4.23.6 Radar Idle Turbo + Exact Repair.

v4.23.7 is a correctness/performance hardening release created after reconstructing and auditing the complete current repository.

## Fixed

- Idle Turbo is now real: AutoScan can borrow up to the configured idle exact-view capacity (default x8) instead of being silently clamped back to the normal x3 traffic limit.
- New Turbo leases automatically stop when a foreground user scan becomes active; normal scan priority remains protected.
- Remote View Worker deadlines now preserve already completed shards instead of discarding the whole remote result when one shard is slow.
- AutoScan recovery therefore retries only the unresolved tail instead of needlessly rechecking healthy exact-view results.
- stale v4.23.5/v4.23.6 release-test contracts were repaired and new behavioral hardening tests were added.

## Validation

- recursive compile: PASS
- runtime global audit: PASS
- release smoke: PASS
- pytest: **253 passed + 167 subtests passed**

See `AUDIT_4_23_7_FULL.md` for the full audit report.

## Deployment

Parser / Bot: **required**.

View Worker replicas: **recommended** from the same checkout for one-version consistency. Page/Date workers do not change behavior in this release; redeploying them from the same checkout is safe but not required. Vinted Scan/Metrics/Session workers are functionally unchanged by v4.23.7.

No manual SQL migration. No new required Railway variable. Existing optional `RADAR_AUTOSCAN_IDLE_VIEW_CONCURRENCY` remains supported; default is 8.
