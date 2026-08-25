# DT PARSER

## v4.11.1 — DT Radar AutoScan Error Recovery

v4.11.1 keeps the stable **v4.11.0 AutoScan + v4.10.2 parser core** and adds targeted recovery for categories that did not finish completely.

### AutoScan error recovery
- every failed/partial AutoScan category is persisted with category name, reason and verified page count
- completion report shows category coverage and page coverage from the requested maximum
- `⚠️ Ошибки круга` opens a paged list of failed categories and their reasons
- `🔁 Повторить только ошибки` starts a low-priority retry round containing only failed categories
- repeated retries keep shrinking to the remaining failures; successful categories are never rescanned
- after a retry, total logical coverage is recomputed against the original round (for example 124/141 + 17 recovered => 141/141)
- completion notification contains direct error/retry buttons when failures remain
- retry uses the same target date as the source round and still yields to foreground user scans
- PostgreSQL state persists the failure list and retry chain across Railway restarts

### Compatibility
A round completed on v4.11.0 contains only the failure count, not the individual category keys. v4.11.1 can display that legacy count, but targeted retry becomes available after a v4.11.1 round has recorded the detailed failures.

See `DEPLOY_V4_11_1_DT_RADAR_ERROR_RECOVERY.md` for rollout and smoke tests.

---

## v4.11.0 — DT Radar AutoScan

v4.11.0 keeps the stable **v4.10.2 DT Radar + Page Cache Recovery** core and adds a persistent low-priority Radar crawler.

### Radar AutoScan
- manual **one round and stop** mode
- optional **one automatic round per day** with admin on/off
- configurable Moscow launch time: 03:00 / 05:00 / 08:00 / 12:00 / 18:00 / 23:00
- scans all 141 leaf categories at **15 pages per category**
- user scans always have priority; AutoScan waits before the next category while foreground scans are active/queued
- real verified views feed category TOP-12 directly into DT Radar without fake user scan cards
- PostgreSQL-persisted progress survives Railway restart and supports Stop / Continue
- admin progress screen, last-20 history and completion notifications
- optional skip of the daily run when a full manual round already completed today

See `DEPLOY_V4_11_0_DT_RADAR_AUTOSCAN.md` for rollout and behavior.

---

## v4.10.2 — DT Radar Page Cache Recovery

v4.10.2 keeps the full **DT Radar** release and all v4.10.1 reliability fixes, then fixes poisoned Page Worker cache replay discovered under live load.

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


### v4.10.2 live-load fix

If a prefetched Page Worker response duplicates a page already seen in the same scan, DT Parser now invalidates that Redis cache entry and forces the affected page through the local stable parser. This prevents stable retries and BrowserContext resets from replaying the same poisoned remote page.

See `DEPLOY_V4_10_2_PAGE_CACHE_RECOVERY.md`.
