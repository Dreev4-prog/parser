# DT PARSER

## v4.10.1 — DT Radar Reliability Fix

v4.10.1 keeps the full **DT Radar** release from v4.10.0 and adds two production fixes discovered under live load.

### View Speed Fix
- large view batches (40+ URLs) are always sharded into small Redis jobs for the four View Worker replicas
- transient 403/429 responses from the official counter get one short HTTP retry before Chromium fallback
- up to two View Worker replicas may run verified browser fallback concurrently while each replica still keeps one local browser fallback lane
- exact view-count acceptance rules are unchanged

### Repeated Page Recovery
- one persistently duplicated nationwide page no longer aborts the entire category
- `repeated-content` is skipped only after the existing retries and BrowserContext recycle fail
- skipped duplicate pages contribute no listings and no confirmed depth
- the scan continues to later pages until the requested verified depth is recovered
- recovery is bounded; challenge, wrong-page identity and other transport failures remain strict

### DT Radar retained
- global persistent Radar product families
- Hot / Rising / Stable / Cooling / Historical states
- AI Picks, all-time ranking, categories and `⭐ Мой Radar`
- completed scan TOP-12 + DT AI signals feed the same Radar database
- four main user lanes; user 5+ waits FIFO
- free-trial and paid scans share the same four lanes

See `DEPLOY_V4_10_1_DT_RADAR_RELIABILITY.md` for rollout and smoke tests.

---

## v4.10.0 — DT Radar

v4.10.0 keeps the v4.9.1 four-lane/free-trial production core and adds **DT Radar**: a global, persistent database of strong product families found by all completed scans and DT AI.

### DT Radar
- shared subscriber-only Radar instead of another per-user TOP
- products are grouped by deterministic identity/family where possible
- every complete scan contributes its TOP-12 by verified real views
- every non-control AI candidate and every later AI observation updates Radar
- current DT Score can rise/cool; Peak Score is preserved
- statuses: Hot / Rising / Stable / Cooling / Historical
- product/signal history is never automatically deleted
- categories, AI Picks, all-time ranking and per-user `⭐ Мой Radar` favorites
- one-time background backfill imports already saved scans and existing AI history
- no additional Kleinanzeigen traffic is created by Radar bookkeeping

### Production core retained
- exactly 4 main user scan lanes; user 5+ waits FIFO
- free-trial and paid scans share the same four lanes
- Date/Page/View helper architecture and parser algorithms are unchanged
- Radar adds four new PostgreSQL tables; no destructive migration

See `DEPLOY_V4_10_0_DT_RADAR.md` for deployment and smoke-test details.

---

## v4.9.1 — Four-Lane Queue Guarantee

v4.9.1 keeps the v4.9.0 Free Trial Launch and hardens the main Telegram parser so its user-facing capacity cannot silently fall from four lanes to one/two because of stale Railway variables.

### Guaranteed main-parser queue
- exactly **4** local user scan consumers are pinned in `bot.py`
- users 1–4 are claimed immediately; user 5+ remains in the visible FIFO queue
- free-trial and paid scans use the **same** four lanes
- `STABLE_SINGLE_SERVICE_MODE`, `MULTIUSER_STABLE_MODE`, stale `MAX_CONCURRENT_JOBS`, or stale `MULTIUSER_LOCAL_WORKERS` values in the parser Railway service can no longer reduce the main bot below four lanes
- PostgreSQL is switched to `running` immediately when a local worker claims the job, so `КТО СЕЙЧАС ПАРСИТ` reflects worker ownership without a parser/browser-setup delay
- startup fails fast if the four-lane contract is ever broken by a future code change

### Free Trial Launch retained
- 2 free scans for never-paid users
- 1 category per free scan
- 15 / 25 pages, maximum 25
- real views, TOP-12 / TOP-50 and XLSX included
- after the free credits: subscription required for new scans

### Paid access keeps the full product
- up to 50 pages
- multiple categories
- repeat/recheck/manual view refresh
- +3 / +6 / +12h automatic measurements

### Worker architecture unchanged
Date Worker / Page Worker / View Worker remain separate Redis-backed helper services and keep their existing replica logic. The four-lane guarantee applies only to the main Telegram `parser` service. Parsing/date/page/view algorithms were not rewritten in this release.
