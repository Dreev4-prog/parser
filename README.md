# DT PARSER 4.15.6 — Bump Resurrection Integrity

**Base:** 4.15.5 AutoScan Recovery Hardening.

Closes the icon-only `Hochschieben` hole and removes old bumped/resurfaced evidence from Organic Radar. Search cards and live detail pages detect semantic Kleinanzeigen bump SVG/assets (including `bumpup`), while the same `external_id` moving to a later displayed publication day is sticky promotion evidence. High views alone are never used as a promotion verdict.

On first deploy the pre-4.15.6 Radar is quarantined immediately. A background one-time sweep marks current bump/reduced IDs dirty and purges their Radar/AI/Lifecycle contributions. Sweep-clean legacy families stay hidden until a fresh v4.15.6 signal resets old snapshots and rebuilds the family from demand-safe evidence. Unknown pre-DT view history receives an Organic Baseline; only later DT-observed growth is used, while genuinely fresh same-day clean listings can still use verified totals. Page/stable cache schemas move to `v4156-bump-resurrection`. DT Demand Score stays 40/20/15/15/10.

See `DEPLOY_V4_15_6_BUMP_RESURRECTION_INTEGRITY.md`.

---

# DT PARSER 4.15.5 — AutoScan Recovery Hardening

**Base:** 4.15.4 Organic Pipeline Correctness.

Hardens the two remaining transient-failure points without loosening Organic rules or DT Demand Score. Date discovery now recycles the scan BrowserContext for persistent `unknown` chronology and actually uses the deterministic sequential locator when fast exponential/binary recovery cannot prove a boundary. The sequential fallback stays fail-closed if any page remains weak. Radar detail admission now uses bounded HTTP retries, a fresh rendered Chromium fallback, and one delayed retry of only the exact blocked candidate before a category becomes `⚠️ допроверка`. AutoScan telemetry records exact UNKNOWN reasons (`http_403`, `challenge`, `weak_document`, `wrong_identity`, transport errors, etc.).

No score/Organic criteria changes. No manual DB migration and no new required Railway variable. Redeploy Parser; AI/Lifecycle should use the same checkout.

See `DEPLOY_V4_15_5_AUTOSCAN_RECOVERY_HARDENING.md`.

---

# DT PARSER 4.15.4 — Organic Pipeline Correctness

**Base:** 4.15.3 Strict Organic Radar Gate.

Fixes the live `clean listings → exact views → Organic Gate → Radar` pipeline without loosening Organic rules. AutoScan now exact-recovers **all** unresolved view counters instead of only a 24-item fallback; any remaining unknown view makes the category `⚠️ допроверка` rather than silently removing candidates. Radar performs a real fail-closed public detail-page integrity check, walks the ranked list until it has up to 12 proven organic positions, stops before backfilling past an UNKNOWN higher rank, and uses retry-idempotent source keys. Legacy Radar families are quarantined through the new additive `radar_products.organic_verified_at` field until fresh strict evidence certifies them. The admin AutoScan panel now exposes the complete selection funnel and separates new vs already-present retry signals. DT Demand Score remains **40/20/15/15/10**.

Redeploy **Parser + all View Workers + AI Worker + Lifecycle Worker** from the same release. No manual SQL and no new Railway variable. Stop any in-progress v4.15.3 AutoScan and start a fresh full round after deploy.

See `DEPLOY_V4_15_4_ORGANIC_PIPELINE_CORRECTNESS.md`.

---

# DT PARSER 4.15.3 — Strict Organic Radar Gate

**Base:** 4.15.2 Organic Demand Integrity.

Closes the remaining cross-process Radar admission race. Every Radar/Lifecycle signal is now re-checked against current PostgreSQL `listings` state **and** the sticky `listing_integrity` registry inside the write transaction; parser integrity writes use the same per-external-ID advisory lock. Radar feeds, search, category counts, price filters, product details, counters and Fast Sold also have a defensive organic read gate: a family is temporarily hidden if any linked association is dirty/unverified, so contaminated aggregate evidence cannot flash before cleanup finishes. Startup cleanup now unions and repairs dirty state from both `listings` and `listing_integrity`. DT Demand Score stays **40/20/15/15/10**; parser detection, worker algorithms, DB schema and Railway variables are unchanged.

Redeploy **Parser + AI Worker + Lifecycle Worker** from the same release. Page/Date can be redeployed from the same commit for fleet consistency; View Worker behavior is unchanged.

See `DEPLOY_V4_15_3_STRICT_ORGANIC_RADAR_GATE.md`.

---

# DT PARSER 4.15.2 — Organic Demand Integrity

**Base:** 4.15.1 DT Demand Score 2.1 Evidence Adaptive.

Makes DT Radar/DT AI demand evidence organic-only without changing the **40/20/15/15/10** score. Paid Kleinanzeigen visibility (`TOP`, Top-Anzeige, Hochgeschoben, Highlight, Galerie and explicit promoted markers) and crossed/reduced-price listings are sticky exclusions. Dirty ads do not receive new demand view samples and do not enter AI cohorts, Radar or Lifecycle. A new additive `listing_integrity` registry prevents a previously paid/reduced external ID from becoming "organic" after the badge/old price disappears. Startup cleanup removes known dirty AI/Radar/Lifecycle contributions and rebuilds affected Radar families from surviving clean signals. Existing raw PriceHistory is retained so `📉 Снижение цены` keeps working. Redis Page cache and stable page-checkpoint payloads are schema-isolated as `v4152-organic`. No new Railway variable; additive DB schema is automatic.

Redeploy **Parser + Page Worker + Date Worker + AI Worker + Lifecycle Worker** from the same release. View Worker behavior is unchanged.

See `DEPLOY_V4_15_2_ORGANIC_DEMAND_INTEGRITY.md`.

---

# DT PARSER 4.15.1 — DT Demand Score 2.1 Evidence Adaptive

**Base:** 4.15.0 DT Demand Score 2.0.

Keeps the single **40/20/15/15/10** DT Demand Score, but fixes live calibration: factors with no real evidence no longer vote as synthetic `0.5` values. Available evidence weights are renormalized, so an exceptional fresh listing can surface from real Relative View Velocity instead of being mechanically pinned near ~70 while future/history data is still unknown. Acceleration/Persistence/Repeatability/Price Fit join only when their evidence exists. Relative Velocity also gets an absolute-demand safety gate so tiny categories cannot produce 88+ from trivial `3 views vs 1 view` differences. Lifecycle remains outside the score. No DB migration or new Railway variable. Redeploy **AI Worker + Parser**.

Expected AI model: `dt-demand-score-v2.1-evidence-adaptive`.

See `DEPLOY_V4_15_1_DT_DEMAND_SCORE_2_1_EVIDENCE_ADAPTIVE.md`.

---

# DT PARSER 4.15.0 — DT Demand Score 2.0

**Base:** 4.14.1 Radar Price & Return UX.

Replaces the DT AI Lab score with one demand-focused 0–100 model: **40% Relative View Velocity / 20% Acceleration / 15% Persistence / 15% Repeatability / 10% Price Fit**. Relative velocity is age-matched; persistence and repeatability are derived from raw view history; extreme price deviations no longer receive an automatic maximum bonus; prior AI confirmation labels are removed from the repeatability score to avoid circular feedback. The +1/+3/+6h checkpoints now recompute the same five-factor formula instead of using the old 45/37/18 dynamic blend. Lifecycle/disappearance remains completely outside DT Demand Score.

AI Lab keeps one main numeric score (`DT Demand Score`) and shows saturation only as a descriptive market diagnostic. No DB migration or new Railway variable. Redeploy **AI Worker + Parser**.

See `DEPLOY_V4_15_0_DT_DEMAND_SCORE_2_0.md`.

---

# DT PARSER 4.14.1 — Radar Price & Return UX

**Base:** 4.14.0 Fast Sold Lifecycle.

Adds a persistent Radar price filter to category/search feeds and preserves exact list context when a product card is opened. Category cards now return to the same category/page/sort/filter with `⬅️ Назад к категории`; search and other feeds keep their own return context. Price matching uses actually observed listing prices from `radar_product_listings`, not only a product-family min/max range. No AI/DT Score, parser, AutoScan, Date/Page/View/Lifecycle algorithm or DB schema change.

See `DEPLOY_V4_14_1_RADAR_PRICE_AND_RETURN_UX.md`.

---

# DT PARSER 4.14.0 — Fast Sold Lifecycle

**Base:** 4.13.0 Simple Referral Promo.

Adds a new DT Radar market-memory signal without changing the proven four user scan lanes, AutoScan page/date/view logic, referrals or Daily Radar:

- new paid Radar mode: **⚡ Fast Sold / Быстро исчезли**;
- fresh strong Radar listings (`DT Score >= 72`) are automatically enrolled in a durable PostgreSQL Lifecycle queue;
- availability checkpoints run at **15 / 30 / 60 / 120 / 180 minutes** after DT first sees the listing;
- one missing response never counts as a sale/disappearance — a second direct detail-page check about **3 minutes later** is required;
- `403`, `429`, timeouts and uncertain pages are treated as **unknown**, never as sold;
- confirmed disappearances keep first-seen, last-seen, disappearance time, lifetime, last views, price and Peak DT Score;
- Fast Sold cards clearly state that Kleinanzeigen does not always disclose whether the ad was sold or manually removed;
- strong signals from AutoScan, normal completed scans and DT AI can all enter Lifecycle;
- a new **Lifecycle Worker** performs only lightweight direct availability checks, isolated from the main parser lanes;
- PostgreSQL is the durable queue, so the worker does **not require Redis**;
- `radar_lifecycle_watches` is created automatically; no manual SQL migration is required.

### Railway
Deploy the new code, then add **one** service named `Lifecycle Worker` from the same repo and give it the same `DATABASE_URL`. The existing `service_launcher.py` detects that name and starts `lifecycle_worker.py`. No `BOT_TOKEN` is needed on that worker and Redis is optional/not used. If a different service name is preferred, set `DT_SERVICE_ROLE=lifecycleworker`.

See `DEPLOY_V4_14_0_FAST_SOLD_LIFECYCLE.md` for rollout and smoke tests.

---

# DT PARSER 4.13.0 — Simple Referral Promo

**Base:** 4.12.3 Daily Radar FSM Hotfix.

Adds the first compact referral growth loop without changing parser/Radar worker algorithms:

- every user has a personal deep link `?start=ref_<telegram_id>`;
- only the **first bot entry of a new Telegram user** can be attributed;
- one referred user can belong to only one referrer;
- promo rule: **2 eligible new users = +1 day of full subscription**;
- reward repeats automatically: 4 users = +2 days, 6 users = +3 days, etc.;
- active subscribers get the day appended to their current expiry; users without an active subscription start from now;
- admin can enable/disable the promo from `Админ-панель -> 👥 Рефералы`;
- referral-link attribution is still counted while the promo is paused, but those paused-period entries do not earn bonus days;
- the user home gets `🎁 Получить день бесплатно` only while the promo is enabled;
- user referral screen shows personal link, total unique entries, promo progress and earned days;
- new `referral_invites` table is created automatically; no manual SQL migration and no new Railway variables.

Redeploy the **parser** service. Other worker code and concurrency remain unchanged.

---

# DT PARSER 4.12.3 — Daily Radar FSM Hotfix

**Base:** 4.12.2 Daily Radar Instant UI.

Fixes the aiogram 3 callback crash `admin_daily_radar_handler() missing 1 required positional argument: 'fsm'`. The handler now receives the injected FSM context as `state: FSMContext`; its Daily Radar config is kept separately as `digest_state`. No Radar/AutoScan/Page/Date/View/AI algorithm changes. Redeploy parser only.

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
