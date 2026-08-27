# DT PARSER 4.12.2 — Daily Radar Instant UI

**Base:** 4.12.1 Daily Radar Manual Control.

This hotfix makes the admin Daily Radar controls fail-fast and visibly responsive. Telegram callbacks are acknowledged before live PostgreSQL/Radar aggregates run, a loading state is shown immediately, live metric/state reads are bounded by timeouts, and the last successful metrics are cached for panel fallback. Manual send still requires fresh metrics and will refuse to broadcast stale data. Time selection/manual time and the v4.11.9 AutoScan view-deadlock fix are preserved.

# DT PARSER 4.12.1 — Daily Radar Manual Control

- Daily Radar: любое время `HH:MM` по Москве.
- Исправлена ошибка «Некорректное время» на кнопках времени.
- `📣 Отправить сейчас` с подтверждением и живыми цифрами.
- Ручная отправка не создаёт автоматический дубль в тот же день.
- База: 4.12.0; Radar/AutoScan/Page/Date/View/AI core сохранён.

## v4.12.0 — Daily Radar Growth Loop

v4.12.0 keeps the full **v4.11.9 AutoScan View Deadlock Recovery + v4.11.8 Launch Recovery + Free Radar funnel** and adds a permanent daily growth loop around DT Radar.

### Daily Radar
- sends one factual Radar digest per day to every registered, non-banned user
- default schedule: **20:00 MSK**; admin can choose 12:00 / 18:00 / 20:00 / 22:00
- uses live AutoScan + persistent Radar database numbers: checked/new listings, daily signals, new Radar products, Hot, Rising, AI Picks, total Radar base and best DT Score
- free users are sent directly into the existing 5-item Radar preview; paid users go to full Radar
- Daily Radar clicks are attributed inside the existing free Radar funnel (`📨 Пришли из Daily Radar`)
- admin section `📨 Daily Radar` provides on/off, time selection, live counters and a test-to-self button
- send date is persisted in `app_settings`, preventing duplicate same-day campaigns after Railway restarts
- admin-only access mode suppresses the commercial digest
- no DB migration, no new Railway variables, and no parser/Page/Date/View/AI algorithm changes

See `DEPLOY_V4_12_0_DAILY_RADAR_GROWTH_LOOP.md` for deployment and smoke tests.

---

## v4.11.9 — AutoScan View Deadlock Recovery

v4.11.9 keeps the full **v4.11.8 AutoScan Launch Recovery + v4.11.7 Free Funnel Analytics + v4.11.6 Free Radar Preview + v4.11.5 AutoScan Stability** release and fixes a deterministic AutoScan stall after the last collected page.

### What was fixed
- Stable single-service mode intentionally sets `TRAFFIC.background_during_scans=0` so background view work never steals capacity from foreground user scans.
- AutoScan also registers its category as an active scan job. Its deferred exact-view phase was using `traffic_priority=background`, so after page 15 the AutoScan could wait forever for a background view lease that its own active scan job prohibited.
- AutoScan deferred views now use a bounded safe lane (`concurrency=4`) only when background lanes are disabled. The global traffic limits and user-facing scan protection remain unchanged.
- Added `Radar AutoScan views start ...` logging before the first counter request so the phase is visible immediately in Railway logs.
- Browser recovery remains capped at 24 unresolved listings and still accepts exact verified counts only.

No database migration and no new Railway variables. Redeploy the **parser** service only.

See `DEPLOY_V4_11_9_AUTOSCAN_VIEW_DEADLOCK_RECOVERY.md` for rollout and smoke tests.

# DT PARSER

## v4.11.8 — AutoScan Launch Recovery

v4.11.8 keeps the full **v4.11.7 Free Funnel Analytics + v4.11.6 Free Radar Preview + v4.11.5 AutoScan Stability** release and hardens only AutoScan launch orchestration.

### AutoScan launch recovery
- manual Start, Resume and Retry immediately kick the AutoScan runner instead of relying only on the background scheduler wake-up
- a single-flight runner lock prevents scheduler/manual races from ever creating two simultaneous AutoScan circles
- a 20-second launch watchdog self-retries a round that remains running but untouched
- fresh circles show `Запуск первой категории…` immediately
- the admin receives explicit feedback when AutoScan is starting now versus waiting for foreground user scans
- parser algorithms, 84-category policy, worker fleet, DT Radar, free preview and funnel analytics remain unchanged
- no DB migration or new Railway variable

See `DEPLOY_V4_11_8_AUTOSCAN_LAUNCH_RECOVERY.md` for rollout and smoke tests.

---

## v4.11.7 — Free Funnel Analytics

v4.11.7 keeps the full **v4.11.6 Free Radar Preview + v4.11.5 AutoScan Stability** release and adds admin-only conversion analytics for the free Radar demo.

### Free Radar funnel
- records only non-subscriber preview actions: Radar open, Best Now, mode open, preview-product open, locked-feature interest and full-access click
- admin funnel shows 24h / all-time distinct-user stages and Radar -> payment conversion
- `5/5` completion is based on 5 unique preview products opened in at least one mode
- recent visitor list shows @username/id, Hot/Rising/AI preview progress, trial-scan usage, upgrade click and payment-after-Radar status
- confirmed payment is attributed only when `paid_at` is after the recorded free Radar visit, so old customers are not falsely counted as conversions
- analytics live inside `Админ-панель -> Бесплатные сканы -> Воронка бесплатного Radar`
- adds the small `free_radar_events` PostgreSQL table automatically; no manual migration or new Railway variable
- public free Radar, paid Radar, AutoScan, DT Score and parser/worker algorithms remain unchanged

Analytics start collecting after v4.11.7 is deployed; earlier v4.11.6 preview activity is not retroactive.

See `DEPLOY_V4_11_7_FREE_FUNNEL_ANALYTICS.md` for rollout and smoke tests.

---

## v4.11.6 — Free Radar Preview

v4.11.6 keeps the full **v4.11.5 AutoScan Stability + v4.11.4 Radar Category Feed** release and adds a conversion-focused read-only Radar preview for users without an active subscription.

### Free Radar Preview
- non-subscribers can open `DT Radar -> Лучшие сейчас` instead of seeing a fully locked Radar screen
- `Горячие`, `Набирают` and `AI Picks` each expose only the first **5 real current products**
- preview products keep DT Score, freshness, category, detail analytics and the current Kleinanzeigen listing link
- after the preview, the UI shows how many products remain locked and offers full Radar access
- Search, Categories, My Radar, Records, pagination and the rest of each feed remain subscription-only
- locked Radar features stay visible with a lock marker so users can understand the paid value before purchase
- existing two free scans remain separate and are still offered when credits remain; Radar preview consumes no trial credit
- active subscribers keep the full Radar UI unchanged
- no database migration, worker change, parser-core change or new Railway variable

See `DEPLOY_V4_11_6_FREE_RADAR_PREVIEW.md` for rollout and smoke tests.

---

## v4.11.5 — Radar AutoScan Stability

v4.11.5 keeps the full **v4.11.4 Radar Category Feed** product UX and hardens the background DT Radar producer after live full-circle testing.

### AutoScan stability
- new circles scan **84 product-oriented leaf categories** instead of all 141 Kleinanzeigen leaves
- Immobilien, Jobs, Dienstleistungen, Unterricht/Kurse, Nachbarschaftshilfe and service-like leaves are excluded from AutoScan only; normal user parsing remains available
- one warm `KleinanzeigenParser` session is reused across the round instead of being recreated for every category
- per-category recovery/checkpoint budgets are reset without discarding the warm session
- partial categories trigger bounded cooldown; unexpected system failures recycle the parser
- admin telemetry separates `⚠️ допроверка` from `❌ системных`
- AutoScan view collection uses the exact official counter for all listings and bounds the heavy browser fallback to 24 misses/category; unresolved stale counters are cleared
- foreground/user scans, DT Score, Radar Category Feed, Page/Date/View/AI workers and the four user lanes are unchanged
- no database migration and no new Railway variables

See `DEPLOY_V4_11_5_AUTOSCAN_STABILITY.md` for rollout and smoke tests.

---

## v4.11.4 — DT Radar Category Feed

v4.11.4 keeps the full **v4.11.3 Simple Home + v4.11.2 Category Navigator + v4.11.1 AutoScan Error Recovery** release and makes category browsing useful as an accumulated curated catalogue instead of a 24-hour-only feed.

### Category feed
- `Категории` still uses the simple two-level structure: large section -> leaf subcategory
- each section count shows the total number of products that have passed DT Radar selection
- each leaf subcategory shows `total · 🆕 new today` when new products were added on the current Moscow day
- opening a subcategory now shows **all Radar-selected products** in that category; historical accepted products are no longer hidden by a 24-hour filter
- default sorting is **newest first** using `first_radar_at`, so an old product cannot jump to the top only because its score was refreshed
- products added within 3 hours are marked `🆕 Новое`; products added earlier today are marked `🟢 Сегодня`; older products show `вчера`, `N дн назад`, or the date
- one simple toggle switches between `🔥 Сначала лучшие` (DT Score) and `🆕 Сначала новые`
- DT Score remains visible on every product
- no database migration, parser algorithm, AutoScan, Page/View/Date/AI worker or subscription changes

See `DEPLOY_V4_11_4_RADAR_CATEGORY_FEED.md` for rollout and smoke tests.

---

## v4.11.3 — DT Radar Simple Home

v4.11.3 keeps the full **v4.11.2 Category Navigator + v4.11.1 AutoScan Error Recovery** release and simplifies the mass-market DT Radar entry flow.

### Simple Radar UX
- Radar home now has four primary actions only: `Лучшие сейчас`, `Поиск`, `Категории`, `Мой Radar`
- Hot / Rising / AI Picks move under `Лучшие сейчас`; all-time history becomes secondary `Рекорды Radar`
- Rising no longer duplicates Hot products
- Radar search finds accumulated products by title/model and keeps DT Score ordering
- product lists and product cards show a human freshness label
- category/subcategory counters now show fresh Radar activity from the last 24 hours
- opening a subcategory shows the same current 24-hour feed instead of the whole historical accumulation
- historical Radar data is preserved and remains available through Search and Records
- no parser, AutoScan, DT Score, database schema or worker architecture changes

See `DEPLOY_V4_11_3_RADAR_SIMPLE_HOME.md` for rollout and smoke tests.

---

## v4.11.2 — DT Radar Category Navigator

v4.11.2 keeps the full **v4.11.1 AutoScan Error Recovery** release and simplifies the user-facing DT Radar category browser.

### Hierarchical categories
- `Категории` now opens one clean list of the 15 large Kleinanzeigen sections
- opening a section shows only its own leaf subcategories
- the old flat 141-category Radar list is removed from the user flow
- section buttons show only the accumulated product count; DT Score remains where it is useful — on product lists/cards
- a product list returns to its parent section instead of throwing the user back to the root category list
- no Radar scoring, database, AutoScan, parser or worker logic is changed

See `DEPLOY_V4_11_2_RADAR_CATEGORY_NAVIGATOR.md` for rollout and smoke tests.

---

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
