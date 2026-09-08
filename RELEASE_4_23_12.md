# DT Parser v4.23.12 — Radar 48H Demand Quality

Base: v4.23.11. This release changes Kleinanzeigen Radar 3.2 only. Public marketing remains DT Radar 3.0. Vinted Radar 1.0, its Follow-up Lane, manual parser accuracy, payments, and category/page policies are unchanged.

## Live vs current demand

Confirmed Radar products may remain in Live for up to 48 hours from their latest valid demand signal. Current HOT/Rising requires evidence no older than six hours. The separate **Сильные за 48 часов** feed shows verified historical HOT evidence without calling it current demand. Peak Score and raw snapshots are preserved. An absence from the first 20 pages is not a sale. A confirmed disappearance excludes only that listing; other available, clean family members can retain their own signals. Current evidence time and the family's retention clock are stored separately. A delayed maintenance job cannot make expired or missing evidence current again. Old History is not bulk-restored.

## Observation quality

The first exact counter remains baseline-only. Baseline reporting now separates newly created cycles, rearmed cycles, and already existing observations; old reports retain an explicit legacy-count label. A weak first interval becomes bounded `exploring`, not terminal quiet: its next follow-up is normally +120 minutes, with at most four accepted checkpoints in the existing six-hour evidence window. Exploration receives a maximum of 30 slots and roughly one eighth of a batch before unused capacity is returned to regular work. Existing leases prevent duplicate claims.

Negative counters enter `rollback_pending` without lowering the trusted anchor. Fresh identity/organic verification and two nondecreasing recovery samples are required before starting a new baseline cycle. No inherited total or rollback rebound becomes fresh demand. A new cycle invalidates prior current evidence; stale or superseded workers cannot publish a previous Score. Existing historical snapshots/favorites are retained.

Category quantiles and ranks use the same finite, nonnegative population. Ties are explicit; a flat cohort cannot manufacture a winner. Sparse cohorts retain conservative absolute gates. The normal P90/P95/P98/P99 framework, 3 views/hour noise floor, and existing persistence/family confirmation requirements remain. Thresholds are not relaxed merely to fill HOT.

## Fast Sold

A bounded early availability-only sample enrolls eligible fresh clean baselines with initial exact views 15..399. It is capped at 300 active early watches globally and eight per category; existing strong watches take queue priority. Early checks cannot create a product or Score. A later genuine strong signal may link the existing watch, but cannot retrospectively qualify a disappearance. The original 15/30/60/120/180-minute schedule and two-direct-miss confirmation remain. UNKNOWN/403/429/timeouts never count as disappearance. Fast Sold is evidence of disappearance, not proof of a sale. A listing's disappearance does not overwrite the family's demand clock or erase other active members. Idempotent lifecycle event records provide early/strong/unknown/disappeared/skipped-late diagnostics.

## Telemetry and migrations

The existing checkpoint journal now records rollback/identity-reset events, exploration, and terminal quiet reasons. New additive columns: `radar_observations.rollback_count`, `rollback_last_views`, `rollback_first_at`, `rollback_last_at`, `provenance_reset_at`; `radar_products.current_signal_at`; `radar_lifecycle_watches.product_key`, `enrollment_source`, `strong_qualified_at` and nullable `product_id`. New `radar_lifecycle_events` table. All migrations use the existing serialized initialization; no manual SQL or new required Railway variable. The current-signal backfill runs only when its column is introduced, never on every startup. Previously invalidated NULL timestamps remain NULL. Existing raw Listing/ViewHistory/RadarSnapshot evidence is not deleted.

## Deployment

1. Back up PostgreSQL and retain the previous deployed commit/ZIP. Finish or pause the current Radar round before the controlled deployment. Do not clear Redis, reset Radar, or delete tables.
2. Apply this archive on top of the exact v4.23.11 repository, preserving paths. Do not apply over v4.23.10 or an unrelated release.
3. Redeploy Parser / Bot and all Lifecycle Worker replicas from the same commit. These roles share the new schema and lifecycle lease/result contract. Page/Date/View and Vinted worker algorithms are unchanged; keep the existing healthy replicas or redeploy them from the same commit for consistency.
4. Confirm the additive migration completed before new Lifecycle workers begin processing. Verify Parser, Lifecycle Worker, PostgreSQL and Redis health. Do not manually run the migration a second time.
5. Open Admin → Radar → Analytics and inspect new/existing/rearmed baseline counts, exploring, overdue checkpoints, rollback/recovery and Lifecycle diagnostics. Allow genuine new checkpoints to accumulate before judging HOT coverage.

Rollback: stop new Lifecycle/Parser jobs, deploy the previous v4.23.11 commit to both roles and retain the database. The additive columns and event table may remain; do not drop them. Old code does not understand new `exploring`/`rollback_pending` states or early watches, so a code-only rollback is an emergency read/stop measure, not a supported way to resume all new queues. Before resuming an older scheduler, reconcile or pause new-state jobs using a reviewed database migration. Restore the pre-upgrade DB backup only if a full coordinated rollback is actually required, acknowledging loss of post-backup observations. Never blindly rewrite unknown counters or mark unfinished watches successful.

## Validation and limits

Local validation uses the full reconstructed source tree, SQLAlchemy SQLite execution, source/AST contracts, Python compile, release smoke, and global-symbol audit. New regressions cover baseline counts, late growth, rollback recovery, ties, leases, 48h freshness, early/strong lifecycle qualifications, two-miss/UNKNOWN behavior, additive DDL, and multi-listing family selection. No live Railway, PostgreSQL fleet, marketplace network, or production database was accessed. The tests cannot establish production speed, request acceptance, or future product-finding accuracy. Monitor real telemetry after deploying; the 48h catalogue does not guarantee a nonempty current HOT feed.
