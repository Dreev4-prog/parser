# DT Parser v4.23.11 — Radar Live & Checkpoint Visibility

Base: v4.23.10. This is a focused Kleinanzeigen Radar 3.2 release. Vinted Radar 1.0 / Follow-up, manual parsing, payments and user-facing demand thresholds are unchanged.

## 1. Bounded-depth absence no longer retires Live

A successful 20-page category scan proves only which listings were observed at that depth. It does not prove that older listings disappeared or lost demand. The AutoScan completion path no longer calls the old category-retirement operation. The compatibility function is now a no-op.

The existing six-hour active observation window and 24-hour Live expiry from the last confirmed demand signal remain unchanged. Fresh verified signals may reactivate a historical product through the normal strict organic admission path. Confirmed disappearance and dirty-listing gates remain authoritative. There is no automatic resurrection of old History: legacy rows do not record enough information to distinguish depth-based retirement from real disappearance. This deliberately avoids restoring an unverified or sold item.

## 2. Durable checkpoint visibility

New additive table `radar_checkpoint_events` records baseline-cycle creation, accepted exact measurements, quiet outcomes and expiry. A unique key on external ID, baseline time, checkpoint number and event type makes retries idempotent. Events are written in the same transaction as the corresponding observation update, so a failed transaction does not create fictitious measurements. No raw cookies, URLs or approximate counters are stored in this journal.

The deep Radar Analytics screen now separates actual measurement coverage from ranking: scheduled/due/leased jobs; overdue >15m and >60m; oldest due age; actual p50/p95 measurement delay; baseline cycles created in the last 24h; cycles with >=1 and >=2 accepted exact repeats; cycles with positive growth; quiet outcomes; and expiry before the first or second repeat. The journal begins at deployment and does not fabricate historical events. Baseline-cycle statistics include terminal and re-armed observations rather than relying on the mutable current observation row.

Aggregates are database-side and read through a 30-second single-flight cache with a bounded UI wait. The AutoScan control screen uses only the cached short summary, so a slow analytics query does not block Stop/Start. A small 7-day journal-retention batch runs during existing idle maintenance. Original Listing, ViewHistory, RadarObservation and RadarSnapshot evidence is untouched.

## Deployment

Apply on top of v4.23.10. Redeploy Parser / Bot from the same commit. The Page, Date, View, Lifecycle and Vinted workers have no functional changes in this patch. `init_db()` creates the new table and indexes using the existing serialized schema-migration path. No manual SQL or new required Railway variables.

Do not clear PostgreSQL or Redis and do not reset Radar. Existing rounds may finish; a fresh full round is not required to activate either change. Historical journal counts build from newly accepted events. A database backup before deployment is recommended.

## Validation and limitations

Local reconstruction: Python compileall, runtime-global audit, release smoke and pytest passed (278 tests, 167 subtests). New executable SQL tests cover bounded-depth retention, 24h expiry, idempotent journal writes, baseline cycles, queue backlog, expiry leases, retention, and PostgreSQL upsert compilation. The test environment uses an in-memory SQLite adapter for SQLAlchemy statements; it does not reproduce the live Railway PostgreSQL fleet or Kleinanzeigen traffic. Production latency and actual site refusals still require Railway telemetry.
