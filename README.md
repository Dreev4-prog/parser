# DT PARSER 4.22.4 — Vinted Admin Lab + Isolated Worker Fleet

Adds an admin-only Vinted parser test UI with hierarchical category selection, live catalog/metric percentages, persistent results and worker diagnostics. Vinted execution is isolated into `Vinted Scan Worker ×2` and `Vinted Metrics Worker ×2` using dedicated Redis streams and `vinted_*` tables; existing Kleinanzeigen Page/Date/View worker logic is untouched. Exact Vinted metrics stay fail-closed: catalog zeroes are never accepted as exact views, and the test Radar mode remains baseline-only until the Exact Views gate passes. See `RELEASE_4_22_4.md`.

---

# DT PARSER 4.22.3 — Vinted Session/OAuth Exact Metrics Probe

The third live Vinted Probe run proved three things: the anonymous catalog is reachable, the global ALL feed is too fast-moving for page-number depth recovery, and anonymous item-detail API requests are blocked while public item HTML still omits exact views/chronology. v4.22.3 therefore tests a realistic category-specific scan by default (`catalog_ids=4,5`), bootstraps through `/catalog` to acquire the normal anonymous web session, probes both current web item API routes, and if they remain blocked attempts Vinted's read-only public mobile OAuth flow before giving up. Public item HTML is no longer used for exact-view measurement because ordinary item-page GETs may contaminate the counter. Missing metrics remain UNKNOWN. Kleinanzeigen production logic is unchanged.

See `RELEASE_4_22_3.md`.

---

# DT PARSER 4.22.2 — Vinted Detail API + Unique Depth Recovery

The second live Vinted Probe run confirmed that the public item HTML matches item identity but does not expose exact `view_count` or upload chronology. It also proved that `pagination.time` is not a reliable frozen snapshot cursor on the live newest-first feed. v4.22.2 therefore tests Vinted's current browser endpoint `/api/v2/items/{id}/details` first, keeps HTML as a fail-closed fallback, and changes pagination correctness from “low duplicates” to bounded recovery of the requested unique depth. Kleinanzeigen production logic remains unchanged.

See `RELEASE_4_22_2.md`.

---

# DT Parser 4.22.0 — Vinted Parser Probe

## 4.22.0 isolated Vinted benchmark

The production Kleinanzeigen stack remains unchanged. A new standalone `Vinted Probe` service benchmarks Vinted catalog speed, item identity, metric coverage and exact-view availability before any Vinted Radar logic is allowed to trust the data. Missing metrics stay UNKNOWN; the probe contains no anti-bot challenge bypass. See `RELEASE_4_22_0.md`.

---

# DT Parser 4.21.16 — Radar 24H Category Handoff

## 4.21.16 live-retention redesign
- Radar still actively measures each DT-owned baseline for **6 hours**.
- Confirmed products remain in the live Radar catalogue for **up to 24 hours**, preventing the evening drain between daily AutoScan rounds.
- First startup restores still-fresh 6–24h products that the previous live TTL had already moved to History, using preserved Radar snapshots only.
- A successful AutoScan category pass retires old live product families that are absent from the newly verified clean category set.
- Partial/failed category passes never clear live Radar products.
- Historical products preserve last confirmed Score and Peak Score; no Radar evidence rows are deleted.
- No database migration, new Railway variable, or worker algorithm change.

See `RELEASE_4_21_16.md`.

---

## Base release: 4.21.14 Radar 3.2 Startup Guard Fix


## Radar scope
- AutoScan: 20 pages, today only.
- Automatic Radar excludes Auto, Immobilien, Jobs, Dienstleistungen, Unterricht & Kurse and Nachbarschaftshilfe.
- The same exclusion policy is applied to user-scan Radar baselines.
- The normal user parser is unchanged; exclusions apply only to Radar analytics.

## Category-adaptive demand
- First exact view counter is baseline only and contributes 0 points.
- <3 views/hour is hard noise.
- Candidate = P90 of the current leaf category.
- Early / Score = P95.
- Strong = P98.
- Hot interval = P99, with persistence or an independent family confirmation required for Hot.
- First scored checkpoint remains capped at 50/100.
- Evaluation remains two-pass so one batch uses one shared category cohort.

## Live / History
- Six-hour expiry moves a product out of the live catalogue without destroying its last confirmed Score/peak.
- Historical rows do not appear in Hot / Rising / Best live lists.
- A historical product may return to live only after a new DT checkpoint confirms demand.

## 4.21.14 startup fixes
- Restores every omitted AutoScan runtime constant, including the category/view watchdogs, backoff values, safe view concurrency and launch watchdog.
- Invalid watchdog environment values fall back safely instead of crashing startup.
- Restores `_radar_autoscan_failure_list()` and removes the dead yesterday-Context branch that referenced a retired helper.
- `prepare_radar_v3_once()` is now permanently non-destructive in normal startup and maintenance. A missing reset marker is repaired without deleting Radar tables.
- Adds a global-symbol audit that fails the release if runtime code references an undefined module global.

## Important migration note
The 4.21.13 deployment shown in Railway executed the old Radar reset before crashing. That reset deleted Radar products/observations/snapshots while preserving Listing/ViewHistory. 4.21.14 will not perform another destructive reset; exact deleted pre-crash Radar scores cannot be reconstructed one-for-one, but new live Radar evidence can rebuild from fresh checkpoints.

## Validation
Run `python -m compileall -q .`, `pytest -q`, `python scripts/release_smoke.py`, and `python scripts/check_runtime_globals.py`.
