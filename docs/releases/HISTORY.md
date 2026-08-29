# DT Parser release history

Consolidated historical deployment notes from releases before 4.20.0. Kept for audit/reference; current behavior is documented in `../RELEASE_4_20_0.md`.

---

## Source: `DEPLOY_V4_10_0_DT_RADAR.md`

# DT PARSER v4.10.0 — DT Radar

## What this release adds

v4.10.0 keeps the v4.9.1 Four-Lane Queue Guarantee and adds **DT Radar** — a global, persistent knowledge base of strong product families found by DT Parser and DT AI.

Radar is not another per-user TOP. All successful scans feed one shared analytical database. Similar listings are grouped into one product family when deterministic identity/family recognition allows it.

## User flow

The main menu now contains:

```text
📡 DT Radar
```

Active-access users can open:

- 🔥 Горячие сейчас
- 🚀 Набирают обороты
- 🧠 AI Picks
- 🏆 Лучшие за всё время
- 🗂 Категории
- ⭐ Мой Radar

Expired/free-trial users see a locked Radar teaser with global counts and a subscription CTA; they do not receive the product list.

## What enters Radar

Two independent signal sources feed the same product record:

1. **Completed scan TOP** — up to TOP-12 listings with verified real view counts from every complete saved scan.
2. **DT AI** — every non-control Early Winner / Product Opportunity candidate. Initial AI score and every later AI observation update the same Radar product.

A scan merge is DB-only and is started in the background after the user scan is finished. It does not add Kleinanzeigen requests and does not delay the result card. AI observations reuse the existing AI/View Worker architecture.

## Product grouping

Radar prefers, in order:

1. deterministic `identity_key` with confidence >= 70;
2. AI `cohort_key` / deterministic family key;
3. one listing-specific fallback key when no safe family can be recognized.

This means repeated Apple TV / console / tool listings can accumulate under one product family instead of flooding Radar with duplicates.

## Persistent history

New PostgreSQL tables:

- `radar_products` — one persistent product family and its live/peak score;
- `radar_product_listings` — distinct listings ever associated with that product;
- `radar_snapshots` — append-only score/signal history;
- `radar_favorites` — per-user `⭐ Мой Radar` watch list.

Radar product rows and snapshots have **no automatic delete path**. A product can cool from Hot to Historical, but it remains searchable/listed historically and keeps its Peak Score.

## DT Score lifecycle

Each product stores:

- current DT Score;
- Peak Score;
- confidence;
- status;
- AI opportunity type;
- signal count;
- AI confirmed count;
- distinct listing count;
- best views / best views-per-hour;
- observed price range;
- latest reason/source;
- first/last Radar timestamps.

Statuses:

```text
🔥 hot
🚀 rising
✅ stable
💤 cooling
🗄 historical
```

Fresh AI/scan signals can increase or decrease the live score. If no fresh signal arrives, an hourly DB-only maintenance pass gradually cools the current score after 72 hours. The product itself and its historical Peak Score are never removed.

## Historical backfill

After deployment the main bot starts a background, idempotent one-time backfill:

- all existing non-control AI candidates are merged into Radar;
- all existing complete saved scans contribute their historical TOP real-view products;
- an `app_settings` marker prevents the full backfill from rerunning on every deploy.

The bot starts polling before the backfill begins, so an old database does not block Telegram availability during migration.

## Existing parser architecture retained

The following parser/traffic engines are unchanged from v4.9.1:

- `parser.py`
- `stable_engine.py`
- `distributed.py`
- `traffic.py`
- `date_manager.py` / `date_worker.py`
- `page_manager.py` / `page_worker.py`
- `view_manager.py` / `view_counter_worker.py`

The four main user lanes from v4.9.1 are retained: users 1–4 run, user 5+ waits FIFO. Free-trial and paid scans share the same four lanes.

## Railway deployment

Deploy the v4.10.0 code to:

- `parser` (required for UI, scan-to-Radar merge, backfill, score maintenance)
- `AI Worker` (required so future AI score changes flow into Radar)

Date/Page/View worker code is unchanged. Redeploying those helpers is optional if Railway deploys all services from the same repository automatically.

No new Railway variable is required.

No destructive PostgreSQL migration is required. SQLAlchemy creates the four new additive Radar tables automatically.

## Smoke test

1. Deploy parser + AI Worker from v4.10.0.
2. Open `📡 DT Radar`; old strong products should begin appearing after the background backfill starts.
3. Complete a new scan with real views; its TOP products should appear in Radar without delaying the scan result.
4. Open a Radar item and verify Current Score, Peak Score, category, listing/signal counts and score history.
5. Add/remove it from `⭐ Мой Radar`.
6. Let AI Worker analyze a new scan; AI Picks / product score should update from the AI signal.
7. Confirm the main parser still starts four `scan-worker-*` tasks and a fifth user waits FIFO.

---

## Source: `DEPLOY_V4_10_1_DT_RADAR_RELIABILITY.md`

# DT PARSER v4.10.1 — DT Radar Reliability Fix

## Base

v4.10.1 is built directly on **v4.10.0 DT Radar**. No Radar feature is removed or rolled back.

This release merges two fixes discovered after v4.10.0 was created:

1. View Speed Fix
2. Repeated Page Recovery

## View Speed Fix

Large foreground view batches are always split into small Redis shards instead of depending on a momentary View Worker heartbeat count. With the default four-worker fleet, a 149-listing job is expected to become up to eight small jobs instead of one 149-URL worker job.

Transient official-counter `403/429` responses get one short exact HTTP retry before verified Chromium fallback. Exactness rules are unchanged. The fleet-wide official HTTP budget remains bounded, and Chromium fallback can run on up to two different worker replicas concurrently while each replica remains locally serialized.

Default runtime prefix for this release:

```text
dtparser:viewcounter:runtime:v4101
```

No new Railway variable is required.

## Repeated Page Recovery

A persistent `repeated-content` page no longer ends the whole category after the normal page retries and BrowserContext recycle.

The recovery path is intentionally narrow:

- repeated page contributes zero listings;
- repeated page contributes zero confirmed depth;
- scanner advances to the next nationwide page;
- later verified pages replace the missing depth;
- if the public nationwide window ends with a repeat-created shortfall, only the missing verified depth may be replaced from independent regional feeds;
- recovery is bounded by `DIRECT_REPEATED_RECOVERY_LIMIT` (default 3);
- `challenge`, `page-identity` and other persistent invalid classes remain strict failures.

## DT Radar retained

All v4.10.0 Radar behavior remains present:

- shared subscriber-only `📡 DT Radar`;
- Hot / Rising / Stable / Cooling / Historical;
- AI Picks;
- all-time ranking;
- categories;
- `⭐ Мой Radar`;
- persistent product/signal history;
- scan TOP-12 and DT AI signals feed the same product family;
- no extra Kleinanzeigen traffic is created by Radar bookkeeping.

## Four-lane queue retained

The v4.10.0 / v4.9.1 Four-Lane Guarantee is retained:

```text
users 1–4 -> parsing
user 5+   -> visible FIFO queue
```

Free-trial and paid users share those same four main lanes.

## Railway rollout

Deploy v4.10.1 to:

- `parser`
- `View Worker` (all 4 replicas)
- `AI Worker`

Date/Page workers contain no release-specific logic change, but deploying all services from the same repository/version is recommended so admin telemetry reports one version everywhere.

No destructive PostgreSQL migration is required. Existing DT Radar tables remain intact.

## Smoke test

1. Confirm startup shows `version=4.10.1` and four local scan workers.
2. Open `📡 DT Radar` and confirm existing Radar data/favorites are still present.
3. Start a today scan with 2 categories / 50 pages.
4. If a page repeats, expect a log like `Repeated page recovery skip ...` and the category should continue instead of immediately becoming partial.
5. On the view stage, a large batch should log `Remote view sharding ... shards=...`; View Worker jobs should be small shards rather than one 100+ URL job.
6. Verify the final view counts remain exact and Radar receives the completed scan TOP in the background.

---

## Source: `DEPLOY_V4_10_2_PAGE_CACHE_RECOVERY.md`

# DT PARSER v4.10.2 — Page Cache Recovery

Built directly on v4.10.1. Full DT Radar is preserved.

## Root cause found in production

A Page Worker-prefetched Redis page could pass page identity/date checks yet have the same listing fingerprint as a previously consumed page. v4.10.1 detected `repeated-content`, but `stable_fetch()` only cleared the local in-process cache. The next retry could therefore read the exact same poisoned Redis value again. BrowserContext reset could not help because the retry never reached the local browser.

## Fix

- Detect repeated fingerprint from a remote Page Worker response.
- Immediately delete the corresponding Redis page cache/pending key.
- Pin that requested page to the local stable parser for the rest of the category locator.
- Normal retries and BrowserContext reset now operate on genuinely fresh local content.
- If the local page itself still repeats, the existing bounded repeated-content recovery remains in force.
- Do not raise the repeat-skip limit: repeated pages are never counted as verified depth.

## Unchanged

- DT Radar and DT AI
- four foreground parser lanes / fifth+ FIFO
- free trials and payments
- Date Worker
- View Speed sharding from v4.10.1
- strict challenge/page-identity handling

## Expected production log

When remote cache poisoning is detected:

```text
Repeated Page Worker cache invalidated category=... page=4 previous=3; forcing local retry
```

The following retry should either become a normal `relation=target` local page or, only if Kleinanzeigen itself still repeats the page, proceed through the bounded repeat recovery.

---

## Source: `DEPLOY_V4_11_0_DT_RADAR_AUTOSCAN.md`

# DT PARSER v4.11.0 — DT Radar AutoScan

v4.11.0 is based on the stable v4.10.2 Page Cache Recovery release and keeps the full DT Radar, four-lane user queue, View Speed Fix and repeated-page/cache recovery.

## New: DT Radar AutoScan

DT Radar can now populate itself even when users do not launch scans.

- scans every real leaf category (group-root aliases are excluded)
- fixed production depth: 15 pages per category
- target date: current Europe/Moscow day at round start
- real view counters are collected by the existing exact Views pipeline
- each category contributes its TOP-12 verified-view listings directly to DT Radar
- AutoScan does not create fake user-facing `UserScan` cards
- Radar signals are idempotent by round/listing, so restart/resume cannot duplicate the same signal

## Two admin modes

### Manual one-shot round

`Admin → 📡 Radar AutoScan → ▶️ Запустить 1 круг`

The bot traverses every leaf category once and then stops automatically.

### Daily round

`🔄 Ежедневный автокруг: ВКЛ/ВЫКЛ`

Preset Moscow launch times: 03:00, 05:00, 08:00, 12:00, 18:00, 23:00.

The daily scheduler performs no more than one automatic round per Moscow calendar day. If enabled after today's configured time and no automatic round has run yet, the round starts at the first available opportunity.

Optional setting: skip the automatic round when a completely successful manual round already finished today.

## Foreground priority

AutoScan is intentionally low priority.

- it does not consume one of the four foreground `scan_worker` queue consumers
- before every new category it checks the foreground running/queued jobs
- if any user scan exists, AutoScan waits
- if a user arrives while an AutoScan category is already running, that small 15-page block finishes and AutoScan yields before starting the next category
- existing Date/Page/View worker architecture is reused

## Persistent progress / restart safety

State is stored in PostgreSQL `app_settings` under `dt_radar_autoscan_v1`.

The persisted state includes:

- round id and mode
- current category index
- target date
- processed/successful/failed category counters
- verified pages
- listings/new listings
- Radar signals added
- daily enabled/time state
- last daily date
- last completed full round date
- last 20 round summaries

A Railway restart resumes a `running` round from the next saved category boundary. A manually stopped round remains `paused` and can be resumed from the admin panel.

## Admin controls

`📡 Radar AutoScan` shows:

- current status
- manual/daily mode
- current category
- progress and percentage
- successful/errors
- verified pages
- listings and new listings
- Radar signals added
- daily on/off
- configured Moscow time
- next automatic launch
- last round summary

Controls:

- `▶️ Запустить 1 круг`
- `⏹ Остановить после категории`
- `▶️ Продолжить круг`
- `🔄 Новый круг`
- `🔄 Ежедневный автокруг: ВКЛ/ВЫКЛ`
- `🕐 Время`
- `✅ Пропускать автокруг после ручного`
- `📜 История кругов`

## Completion notification

Every completed round sends admins a Telegram summary with category success/errors, verified pages, listings, new listings, Radar signals and elapsed time.

## Deployment

Deploy the same v4.11.0 commit to:

- parser
- Page Worker replicas
- Date Worker replicas
- View Worker replicas
- AI Worker

No new Railway variables are required. The daily schedule is controlled from the Telegram admin panel and persisted in PostgreSQL.

---

## Source: `DEPLOY_V4_11_1_DT_RADAR_ERROR_RECOVERY.md`

# DT PARSER v4.11.1 — DT Radar AutoScan Error Recovery

Base: v4.11.0 DT Radar AutoScan / v4.10.2 parser reliability core.

## What changed

- Stores each failed AutoScan category with key/name/reason/verified pages.
- Adds category coverage and requested-page coverage to completion reports/history.
- Adds paginated `Ошибки последнего круга` admin view.
- Adds `Повторить только ошибки`; only failed categories are rescanned.
- Retry rounds preserve the original target date and foreground-user priority.
- Retry completion recomputes logical coverage against the original round; full recovery reports 141/141.
- Remaining failures can be retried again without rescanning already recovered categories.
- Direct error/retry buttons are attached to completion notifications when failures remain.

## Important upgrade note

v4.11.0 stored aggregate `failed=N` but did not store failed category keys. Therefore a round that already finished before this upgrade cannot be targeted exactly from its old summary. Starting with the first v4.11.1 round, detailed errors are persisted and targeted retry is available.

## Railway

No new variables are required. Deploy the main `parser` service. Date/Page/View/AI algorithms are unchanged by this release.

## Smoke test

1. Open Admin -> Radar AutoScan.
2. Start one round.
3. If one or more categories finish partial, the final report must contain `Ошибки круга` and `Повторить только ошибки`.
4. Open errors and verify category/reason/page count.
5. Press retry. Progress total must equal only the number of failures, not 141.
6. If all retry categories succeed, final coverage must report the original total fully covered (e.g. 141/141).
7. Verify normal user scans still pause the start of the next AutoScan category.

---

## Source: `DEPLOY_V4_11_2_RADAR_CATEGORY_NAVIGATOR.md`

# DT PARSER v4.11.2 — DT Radar Category Navigator

Base: v4.11.1 DT Radar AutoScan Error Recovery.

## What changed

DT Radar category navigation is now hierarchical instead of one flat list of every leaf category.

### Level 1 — large sections
`DT Radar -> Категории` shows the 15 main Kleinanzeigen sections in one list, for example:

- Auto, Rad & Boot
- Immobilien
- Haus & Garten
- Mode & Beauty
- Elektronik
- Haustiere
- Familie, Kind & Baby
- Jobs
- Freizeit, Hobby & Nachbarschaft
- Musik, Filme & Bücher
- Eintrittskarten & Tickets
- Dienstleistungen
- Verschenken & Tauschen
- Unterricht & Kurse
- Nachbarschaftshilfe

The number on the right is the total number of Radar products accumulated across that section's leaf categories.

### Level 2 — subcategories
Opening a large section shows only its real leaf subcategories. For example `Elektronik` shows Handy & Telefon, Haushaltsgeräte, Audio & Hifi, Foto, Konsolen, Laptops & Notebooks, PCs, PC-Zubehör & Software, Tablets & Reader, TV & Video, Videospiele, Wearables, etc.

Selecting a subcategory opens the existing Radar product list. DT Score, Peak Score and all product analytics are unchanged.

### Back navigation
- product list -> returns to its parent large section
- parent section -> returns to all large sections
- all large sections -> returns to DT Radar home

## Unchanged

- DT Score and product ranking
- DT Radar database and product history
- AutoScan 141 leaf categories / 15 pages
- AutoScan Error Recovery / retry only failures
- Page Cache Recovery
- View / Date / Page / AI worker behavior
- four foreground user parser lanes

## Railway

No new variables or migrations are required.

Only the main `parser` service needs redeployment. Date/Page/View/AI workers are unchanged.

## Smoke test

1. Open `DT Radar -> Категории`.
2. Verify a single list of 15 large sections is shown.
3. Open `Elektronik`.
4. Verify only Elektronik subcategories are shown.
5. Open `Konsolen` (or another subcategory).
6. Verify Radar products still show DT Score.
7. Press Back and verify it returns to Elektronik, not the full 141-category list.
8. Verify Admin -> Radar AutoScan still works normally.

---

## Source: `DEPLOY_V4_11_3_RADAR_SIMPLE_HOME.md`

# DT PARSER v4.11.3 — DT Radar Simple Home

Base: v4.11.2 DT Radar Category Navigator.

## Goal

Make DT Radar understandable from the first screen without removing DT Score or the accumulated historical database.

## New Radar home

The old six equal-weight analytics buttons are replaced by four obvious user actions:

- `🔥 Лучшие сейчас`
- `🔎 Поиск`
- `🗂 Категории`
- `⭐ Мой Radar`

`🏠 Меню` remains as navigation back to the bot.

## Лучшие сейчас

`🔥 Лучшие сейчас` opens a second level with:

- `🔥 Горячие`
- `🚀 Набирают`
- `🧠 AI Picks`

Historical all-time ranking is no longer a primary home action and is shown as the secondary `🏆 Рекорды Radar` entry.

Hot and Rising are now separate feeds: `Набирают` contains only products whose current Radar status is `rising`, instead of duplicating current Hot products.

## Radar search

`🔎 Поиск` accepts a normal product/model phrase (for example `Apple TV` or `PlayStation Portal`) and searches the accumulated Radar database by product title.

Results are ordered by current DT Score and freshness and support pagination. Historical products remain searchable.

## Freshness label

Radar list/search cards now show a human freshness label based on the latest Radar signal:

- `только что`
- `N мин назад`
- `N ч назад`
- `вчера`
- `N дн назад`

The product detail card also shows the same freshness label while retaining the exact latest signal timestamp.

## Category activity counters

The hierarchical category navigator from v4.11.2 is retained, but counts now mean **fresh Radar products active in the last 24 hours**, not the entire historical accumulation.

- main section count = sum of its fresh leaf-category products
- subcategory count = fresh products in that subcategory
- opening a subcategory shows the same current 24-hour feed, ordered by DT Score/freshness

Historical products are not deleted. They remain accessible through Search and Radar Records.

## Unchanged

- DT Score and Peak Score formulas
- Radar product/snapshot/history tables
- AutoScan 141 categories / 15 pages
- AutoScan Error Recovery / retry only failures
- Page Cache Recovery
- Date/Page/View/AI worker behavior
- four foreground parser lanes
- subscription/free-trial logic

## Railway

No new variables and no database migration are required.

Redeploy only the main `parser` service. Date/Page/View/AI workers are unchanged.

## Smoke test

1. Open `DT Radar` and verify only four primary Radar actions are shown.
2. Open `Лучшие сейчас`; verify Hot / Rising / AI Picks plus secondary Records.
3. Open `Набирают`; verify products are current Rising products and contain freshness labels.
4. Open `Поиск`, send `Apple TV` (or another known phrase), and verify paginated results with DT Score + freshness.
5. Open `Категории -> Elektronik`; verify subcategory buttons have current 24h counters.
6. Open a subcategory and verify the list is a fresh 24h feed and still displays DT Score.
7. Open a product and verify exact last-signal time plus human freshness label.
8. Verify Admin -> Radar AutoScan and retry-errors screens still work.

---

## Source: `DEPLOY_V4_11_4_RADAR_CATEGORY_FEED.md`

# DT PARSER v4.11.4 — Radar Category Feed

Base: v4.11.3 DT Radar Simple Home.

## Goal

Keep the new simple category navigation, but make each category a permanent curated Radar catalogue instead of showing only the last 24 hours. New products must still be obvious and appear first.

## User flow

`DT Radar -> Категории -> large section -> subcategory`

The leaf-category button now shows:

`📂 Konsolen · 184 · 🆕 17`

- `184` = all products in this leaf category that entered DT Radar
- `🆕 17` = products whose `first_radar_at` is today in Moscow time

The large-section button keeps one uncluttered total count.

## Category product feed

The 24-hour category filter from v4.11.3 is removed. All accepted Radar products remain visible.

Default order:
1. newest `first_radar_at`
2. DT Score
3. latest signal time

This means a historical product receiving another measurement does not masquerade as a newly discovered product.

Freshness labels in category feeds:
- under 3 hours: `🆕 Новое · N мин/ч назад`
- same Moscow calendar day: `🟢 Сегодня`
- previous day: `вчера`
- within a week: `N дн назад`
- older: calendar date

One toggle is available:
- default feed: `🔥 Сначала лучшие`
- best feed: `🆕 Сначала новые`

`Сначала лучшие` sorts the same accumulated category by current DT Score, then freshness.

## Unchanged

- DT Score / Peak Score calculations
- Simple Radar home and Search
- Hot / Rising / AI Picks / Records
- Radar AutoScan 141 leaf categories / 15 pages
- AutoScan Error Recovery
- Page Cache Recovery
- Date/Page/View/AI workers
- four user parser lanes
- database schema and subscriptions

## Railway

No new Railway Variables and no database migration. Redeploy only the main `parser` service.

## Smoke test

1. Open `DT Radar -> Категории`; verify large sections show accumulated totals.
2. Open a large section such as `Elektronik`; verify leaf buttons show total counts and `🆕 today` where applicable.
3. Open a subcategory; verify results are not limited to the last 24 hours.
4. Verify recently added products appear first and show `🆕 Новое` or `🟢 Сегодня`.
5. Press `🔥 Сначала лучшие`; verify the same category is reordered by DT Score.
6. Press `🆕 Сначала новые`; verify newest ordering returns.
7. Verify pagination preserves the selected ordering.
8. Verify Admin -> Radar AutoScan and retry-errors still open normally.

---

## Source: `DEPLOY_V4_11_5_AUTOSCAN_STABILITY.md`

# DT PARSER v4.11.5 — Radar AutoScan Stability

Base: v4.11.4 Radar Category Feed.

## Why this release

Live AutoScan showed a high partial-failure rate while walking all 141 Kleinanzeigen leaf categories. The background Radar producer was treating service/non-product feeds exactly like normal product feeds, recreating a parser/browser session for every category, and verifying views for every matched listing with the same heavy path used by foreground scans.

v4.11.5 changes only the Radar AutoScan producer. Normal user scans, DT Radar category browsing, DT Score, subscriptions and the Date/Page/View/AI worker architecture stay intact.

## 1. Product-only AutoScan policy

New AutoScan rounds contain **84 product-oriented leaf categories** instead of all 141 leaves.

Excluded from AutoScan only:
- whole groups: Immobilien, Jobs, Dienstleistungen, Unterricht & Kurse, Nachbarschaftshilfe
- service/non-product leaves inside otherwise useful groups, such as Reparaturen & Dienstleistungen, Dienstleistungen Haus & Garten, Dienstleistungen Elektronik, Tierbetreuung, Altenpflege, Babysitter, Reise/Eventservices, Verloren & Gefunden, etc.

These categories are **not removed from the normal parser**. A user can still scan them manually where supported.

A persisted v4.11.4 round that was already running keeps its original index/order so a Railway deploy cannot corrupt progress. From the moment v4.11.5 is deployed, remaining non-product leaves in that legacy round are skipped without network traffic. Every newly started round uses the 84-category policy from the start.

## 2. One warm parser session per round

v4.11.4 created and closed `KleinanzeigenParser()` for every AutoScan category.

v4.11.5:
- creates one parser for the round
- reuses the same HTTP/browser session between healthy categories
- resets only per-category checkpoints/recovery budgets before the next category
- resets the browser context after a partial crawl
- recreates the whole parser only after an unexpected system failure

This removes dozens of unnecessary BrowserContext/session startups from every complete circle.

## 3. Adaptive cooldown

AutoScan no longer goes directly from one weak category into the next one.

- successful category: tiny 0.25s gap
- partial/temporary category: bounded 3s -> 6s -> 12s -> 24s -> max 30s cooldown
- system error: bounded 8s -> 16s -> max 30s cooldown
- a successful category resets the failure streak

Foreground users still have priority before every next category.

## 4. Partial vs system errors

Admin telemetry now separates:
- `✅ Успешно`
- `⚠️ допроверка` — incomplete date/page evidence, temporary limits/timeouts, other recoverable crawl quality issues
- `❌ системных` — unexpected execution/code failures

The saved problem list stores `kind=partial|system`, reason and verified-page count. Retry still runs only the currently problematic product categories.

## 5. Lower-pressure exact views for AutoScan

Foreground/user scans keep the existing Accurate Views path unchanged.

AutoScan uses a dedicated lower-pressure measurement path:
1. every matched listing is queried through Kleinanzeigen's public official `s-vac-inc-get` counter endpoint;
2. explicit parsed counter values are persisted as exact current views;
3. only up to 24 official-endpoint misses per category enter the heavier verified View Worker/browser recovery path;
4. unresolved rows are cleared instead of reusing stale views;
5. `record_autoscan_hot()` still promotes only listings with a freshly verified numeric view count.

This keeps counters shown/promoted by AutoScan exact while avoiding hundreds of unnecessary Chromium fallbacks per category.

## 6. Preserved behavior

Unchanged:
- Radar Category Feed / new-first and best-first sorting
- DT Score and Radar scoring
- Radar Search / My Radar / Best Now
- Page Cache Recovery
- Date/Page/View/AI workers
- four user parser lanes and FIFO user queue
- Free Trial and subscriptions
- PostgreSQL schema

## Railway

No new variables and no DB migration.

Redeploy **parser** for the AutoScan changes. `parser.py` is part of the main parser service. Dedicated Date/Page/View/AI worker entrypoints do not need a redeploy for this release.

## Expected log markers

Startup:

`v4.11.5 AutoScan Stability + Radar Category Feed + Simple Home online`

Category start:

`DT Radar AutoScan category start ... parser_reused=True`

AutoScan views:

`Radar AutoScan views complete category=... total=... exact=... unresolved=... browser_recovery=.../...`

Cooldown after a weak category:

`DT Radar AutoScan cooldown kind=partial ...`

## Smoke test

1. Deploy parser and confirm startup reports v4.11.5.
2. Stop an old v4.11.4 circle if you want a clean test, then press `Запустить 1 круг`.
3. Confirm the admin screen says **84 товарных категорий**.
4. Confirm Immobilien/Jobs/Dienstleistungen/Kurse/Nachbarschaftshilfe do not appear as AutoScan work.
5. Confirm logs show `parser_reused=True` across consecutive successful categories.
6. Confirm a weak category becomes `⚠️ допроверка`, not a generic crash.
7. Confirm an unexpected exception would be counted separately under `❌ системных`.
8. Confirm `Повторить проблемные` contains only product categories that still need recovery.
9. Open DT Radar/category feeds and verify DT Score/freshness/category browsing are unchanged.

---

## Source: `DEPLOY_V4_11_6_FREE_RADAR_PREVIEW.md`

# DT PARSER v4.11.6 — Free Radar Preview

Base: v4.11.5 AutoScan Stability.

## Goal

Give non-subscribers a real, useful DT Radar demo instead of a completely locked screen. The preview must demonstrate live product value without exposing the full accumulated database.

## Free user flow

A user without an active subscription can now open:

`DT Radar -> Лучшие сейчас`

They may choose:
- `Горячие`
- `Набирают`
- `AI Picks`

Each mode shows only the first **5 real current Radar products**. The preview includes the same DT Score, freshness, category and product detail card used by the paid product. The current Kleinanzeigen listing can be opened from the preview product card when an active listing URL exists.

After the five visible products, the bot shows how many additional products are hidden and offers `Открыть полный DT Radar`.

## Locked in free mode

The following remain subscription-only:
- all results after the first 5 in each Best Now mode
- pagination
- Radar Search
- Categories and subcategories
- My Radar / favorites
- Radar Records

Locked buttons remain visible on the Radar home with a lock marker so users understand what the paid product contains.

## Free scans remain separate

The existing two-scan free trial is not changed. If the user still has a trial scan remaining, the free Radar result screen also shows the existing `Бесплатный скан` action. Radar preview does not consume scan credits.

## Access behavior

- active subscriber/admin: full Radar unchanged
- non-subscriber in subscription mode: read-only 5-product preview
- banned user: no preview
- admin-only mode: no public preview
- open mode: full access follows the existing access policy

Free preview product callbacks are isolated from paid `radaritem` callbacks. Search/category/favorite callbacks are not exposed by the free keyboard.

## Preserved behavior

Unchanged from v4.11.5:
- 84-category product-only AutoScan
- one warm parser session per AutoScan round
- AutoScan cooldown/retry/error classification
- exact official view-counter path and bounded browser fallback
- Radar Category Feed and DT Score
- Page/Date/View/AI workers
- four user parser lanes and FIFO queue
- subscription plans and payment providers
- PostgreSQL schema

## Railway

No new variables and no DB migration.

Redeploy only **parser**. Dedicated Date/Page/View/AI worker entrypoints are unchanged.

## Smoke test

1. Deploy parser and confirm startup reports v4.11.6.
2. Open the bot from an account without an active subscription.
3. Confirm the main menu shows `DT Radar` instead of a fully locked Radar button.
4. Open `DT Radar -> Лучшие сейчас -> Горячие` and confirm exactly up to 5 real products are shown.
5. Open one preview product and confirm DT Score/freshness/detail + current Kleinanzeigen link are visible.
6. Confirm the preview product card has no `Добавить в Мой Radar` action.
7. Confirm page 2, Search, Categories, My Radar and Records lead to the full-access upsell.
8. If trial credits remain, confirm the free-scan button is present and Radar preview does not decrement the credit.
9. Repeat from an active paid account and confirm full Radar remains unchanged.

---

## Source: `DEPLOY_V4_11_7_FREE_FUNNEL_ANALYTICS.md`

# DT PARSER v4.11.7 — Free Funnel Analytics

Base: v4.11.6 Free Radar Preview.

## Goal

Measure whether the new free DT Radar preview actually leads users toward a paid subscription. The analytics are admin-only and must not change the public Radar experience or parser behavior.

## What is tracked

Only actions performed by users who do **not** currently have full access are recorded:
- opened DT Radar
- opened `Лучшие сейчас`
- opened Hot / Rising / AI Picks preview
- opened a preview product
- clicked a locked Radar feature (Search / Categories / My Radar / Records)
- clicked `Открыть полный DT Radar`

For product-preview completion the event keeps only the internal Radar product id. This makes it possible to tell whether the visitor actually opened all 5 unique demo products in a mode.

No message text, search text, external browsing history or Kleinanzeigen activity is stored by this funnel.

## Admin UI

Open:

`Админ-панель -> Бесплатные сканы -> Воронка бесплатного Radar`

The funnel shows **last 24 hours / all time**:
- Radar visitors
- `Лучшие сейчас` visitors
- users who selected a preview mode
- users who opened at least one preview product
- users who opened all 5 unique demo products in at least one mode
- users who clicked full access
- users who paid **after** their first recorded free Radar visit
- Radar -> payment conversion

The free-scans dashboard also shows a compact Radar summary: visitors, full-access clicks, purchases after Radar and conversion.

## Recent visitors

The funnel includes a paged list of recent visitors. For each user the admin sees:
- @username / first name and Telegram user id
- last Radar activity time
- number of Radar and `Лучшие сейчас` opens
- unique preview products opened in Hot / Rising / AI Picks, e.g. `🔥 5/5 · 🚀 2/5 · 🧠 0/5`
- whether full access was clicked
- free scan usage
- whether a payment happened after the free Radar visit
- which locked Radar areas were attempted

Each visitor row has a button to open the existing admin user card.

## Conversion attribution

A historical payment does not count as a Radar conversion. A user is counted as `Купили после Radar` only when a confirmed paid `SubscriptionPayment.paid_at` is at or after that user's first recorded free Radar visit in the measured cohort.

This prevents an expired former subscriber who later tries the free preview from being falsely counted as a new Radar conversion.

## Database

v4.11.7 adds one small append-only table:

`free_radar_events`

On PostgreSQL it is created with `CREATE TABLE IF NOT EXISTS` before the normal SQLAlchemy metadata pass so simultaneous Railway service startup cannot race on the new table. Indexes are created for user, event type, mode, feature, product and timestamp.

No manual migration or new Railway variable is required.

Analytics begin accumulating after v4.11.7 is deployed; v4.11.6 preview actions that happened before deployment cannot be reconstructed.

## Preserved behavior

Unchanged from v4.11.6/v4.11.5:
- free Radar preview remains 5 real products per Hot / Rising / AI Picks mode
- Search / Categories / My Radar / Records remain locked for free users
- two free parser scans remain separate
- paid Radar remains unchanged
- 84-category product-only AutoScan
- DT Score / Radar Category Feed
- Page / Date / View / AI workers
- four foreground parser lanes and FIFO queue
- subscription plans and payment providers

## Railway

Redeploy **parser**. The parser creates the analytics table automatically. Dedicated workers do not need the new feature to continue operating.

If the same repository deploy automatically rebuilds the worker services too, the `IF NOT EXISTS` table setup is safe for concurrent startup.

## Smoke test

1. Deploy parser and confirm startup reports `version=4.11.7`.
2. From a non-subscribed account open `DT Radar`.
3. Open `Лучшие сейчас -> Горячие` and open 1-2 products.
4. Click `Открыть полный DT Radar` once.
5. Open admin -> `Бесплатные сканы` and confirm the Radar visitor/click counters increased.
6. Open `Воронка бесплатного Radar` and confirm the visitor appears with the correct mode/product progress.
7. Open the same flow from a paid account and confirm it does not add free-funnel events.
8. Confirm normal parser scans, AutoScan and paid Radar work unchanged.

---

## Source: `DEPLOY_V4_11_8_AUTOSCAN_LAUNCH_RECOVERY.md`

# DT PARSER v4.11.8 — AutoScan Launch Recovery

Base: **v4.11.7 Free Funnel Analytics**.

## Why this release exists

A manual Radar AutoScan could be persisted as `status=running` while actual execution still depended on the background AutoScan scheduler noticing the wake-up. If that scheduler was delayed/stuck between iterations, the admin could press **Запустить 1 круг** and see no category begin.

The parser/date/page/view algorithms themselves were not changed by v4.11.7. v4.11.8 hardens only the orchestration of starting/resuming an AutoScan round.

## Changes

- manual **Start** immediately kicks the AutoScan runner; it no longer depends only on scheduler wake-up
- **Resume** and **Retry failed** use the same immediate kick
- a dedicated single-flight lock guarantees scheduler + manual kick can never run two AutoScan parser rounds at once
- 20-second launch watchdog re-kicks a round if it is still `running` but untouched and not waiting for user scans
- a fresh round immediately displays `Запуск первой категории…` instead of an empty current-category line
- admin callback explicitly says either:
  - `Круг запущен · первая категория стартует сейчас`, or
  - `Круг запущен · ждёт пользовательские сканы: N`
- new launch logs:
  - `DT Radar AutoScan immediate kick reason=manual-start`
  - `DT Radar AutoScan runner entered ...`
  - `DT Radar AutoScan category start ...`
  - watchdog re-kick only if launch did not advance

## Unchanged

- 84 product-oriented AutoScan categories
- one warm parser session per circle
- product-only policy / cooldown / partial recovery
- exact Radar view collection
- DT Score / Radar feeds / free Radar preview / free funnel analytics
- Date/Page/View/AI worker code
- four foreground parser lanes

## Railway

No new variables and no DB migration.

Redeploy **parser service only**.

## Smoke test

1. Open Admin -> Radar AutoScan.
2. Press **Запустить 1 круг**.
3. With no user scans active, callback must say `первая категория стартует сейчас`.
4. Logs should immediately show `immediate kick`, then `runner entered`, then `category start`.
5. If a user scan is active/queued, AutoScan should visibly wait and start automatically when the foreground queue becomes empty.
6. Stop after category, then Resume; Resume should start immediately via the same runner path.

---

## Source: `DEPLOY_V4_11_9_AUTOSCAN_VIEW_DEADLOCK_RECOVERY.md`

# DT PARSER v4.11.9 — AutoScan View Deadlock Recovery

Base: v4.11.8.

## Root cause
In stable single-service mode `TRAFFIC.background_during_scans` is forced to `0`. AutoScan wraps an entire category in `TRAFFIC.scan_job_started()`, including the deferred exact-view phase. v4.11.8 asked for those AutoScan counters with `traffic_priority="background"`. Therefore, after the last category page completed, every counter request waited for a background lease while the same AutoScan job kept `scan_jobs_active > 0`. The first counter request could never start.

The Railway signature is: all page logs finish (for example `page=15 relation=target`) and then no `Radar AutoScan views ...` and no `category finish` lines.

## Fix
- AutoScan exact counters switch to a bounded `normal` traffic lane only when background views are disabled by stable mode.
- Lane size is capped at 4 concurrent direct requests.
- Existing global traffic pool, scan reservation, FIFO queue, and user scan protection are unchanged.
- The verified browser fallback uses the same safe priority and remains capped at 24 unresolved URLs.
- Added an explicit `Radar AutoScan views start` log line.

## Expected logs
After the final collected page you should see: 

```text
Radar AutoScan views start category=Autos total=... priority=normal concurrency=4 scan_jobs_active_safe_mode=True
Radar AutoScan views direct category=Autos checked=50/...
Radar AutoScan views complete category=Autos ...
DT Radar AutoScan category finish ... complete=True
DT Radar AutoScan category start ... index=2/84 ...
```

## Deploy
Redeploy **parser service only**. No DB migration. No new Railway variables.

---

## Source: `DEPLOY_V4_12_0_DAILY_RADAR_GROWTH_LOOP.md`

# DT PARSER v4.12.0 — Daily Radar Growth Loop

Base: **v4.11.9 AutoScan View Deadlock Recovery**.

## What changed

v4.12.0 adds a permanent daily DT Radar marketing digest. It uses only live persisted metrics and sends one message per Moscow calendar day to every registered, non-banned bot user.

Default schedule: **20:00 MSK**.

The digest includes, when available:
- AutoScan listings checked today
- new listings seen by AutoScan today
- categories processed today
- Radar signals recorded today
- new Radar products added today
- current Hot / Rising / AI Picks totals
- total persistent Radar product base
- best DT Score recorded today

Free users get a CTA to the existing five-item Radar preview and full-access button. Active subscribers get a shorter CTA to their already-unlocked Radar.

## Funnel tracking

The `📡 Открыть DT Radar` button uses a dedicated callback. Free-preview users who enter through the daily digest are recorded as `daily_digest_open` and also as a normal `radar_open`, so the existing Free Radar funnel keeps its full conversion chain.

Admin funnel now includes:

`📨 Пришли из Daily Radar: 24h / all-time`

## Admin controls

Open:

`Админ-панель -> 📨 Daily Radar`

Available controls:
- enable / disable Daily Radar
- choose 12:00 / 18:00 / 20:00 / 22:00 MSK
- send a test only to the current admin
- refresh live numbers

The admin page also shows the next run, last send, last delivered count, and today's live Radar counters.

## Restart safety

The setting and `last_sent_date` are persisted in the existing `app_settings` table. The send date is reserved before fan-out, so a Railway restart cannot cause the same daily campaign to be sent twice.

On the first deployment only: if v4.12.0 is installed after 20:00 MSK, the system does **not** immediately blast the audience. It begins the automatic cadence the next day. If installed before 20:00 MSK, the first automatic digest can go out at 20:00 the same day.

## Delivery behavior

Recipients: all registered `bot_users` where `is_banned = false`, including expired subscribers. Telegram `RetryAfter` is respected and sending is throttled to stay below common bot broadcast limits.

Daily commercial delivery is suspended automatically while the project access mode is `admin-only`.

## Deployment

No database migration and no new Railway variables.

Redeploy **parser service only**. Dedicated Date/Page/View/AI workers are unchanged.

## Smoke test

1. Confirm startup log contains `version=4.12.0`.
2. Open `Админ-панель -> 📨 Daily Radar`.
3. Confirm status is ON and default time is 20:00 MSK.
4. Press `🧪 Тест только мне` and verify the message contains live numbers and `📡 Открыть DT Radar`.
5. From a free account, press the daily Radar button and confirm the normal five-item preview opens.
6. Check `Бесплатные сканы -> Воронка бесплатного Radar`; `📨 Пришли из Daily Radar` should increment for a free-preview account.

---

## Source: `DEPLOY_V4_12_1_DAILY_RADAR_MANUAL_CONTROL.md`

# DT PARSER 4.12.1 — Daily Radar Manual Control

Base: **4.12.0 Daily Radar Growth Loop**.

## Что исправлено

- Исправлена ошибка кнопок времени Daily Radar: callback больше не теряет часы из `HH:MM`.
- Время больше не ограничено готовыми пресетами: админ может ввести любое московское время `00:00–23:59`.
- Пресеты 12:00 / 18:00 / 20:00 / 22:00 сохранены для быстрого выбора.
- Добавлена кнопка **📣 Отправить сейчас**.
- Перед массовой отправкой показывается подтверждение и актуальные цифры.
- Ручную Daily Radar рассылку можно запускать в любое время, даже если автоматический режим выключен.
- Ручная отправка помечает дневной digest как уже отправленный, поэтому автоматическая рассылка в тот же день не дублируется. При желании админ всё равно может нажать «Отправить сейчас» повторно вручную.
- Добавлена защита от одновременной двойной отправки.

## Деплой

Новых Railway Variables нет. Миграция PostgreSQL не нужна.

Передеплоить только **parser service**.

---

## Source: `DEPLOY_V4_12_2_DAILY_RADAR_INSTANT_UI.md`

# DT PARSER 4.12.2 — Daily Radar Instant UI

Base: **4.12.1**.

## Fixed

- Admin `📨 Daily Radar` acknowledges the Telegram callback immediately.
- The screen first shows `⏳ Загружаю живые цифры…` instead of appearing dead.
- Daily Radar state reads, live Radar aggregates and recipient counting have bounded timeouts.
- Last successful live metrics are cached in the existing `AppSetting` JSON and can be displayed as a fallback.
- If fresh metrics cannot be obtained, manual broadcast is **not** sent with stale/zero data.
- `Время`, `Ввести своё время`, `Отправить сейчас` and `Тест только мне` now all acknowledge first and surface an explicit error instead of hanging silently.
- Added diagnostic log entry: `Daily Radar admin panel open requested admin=...`.

## Preserved

- Arbitrary `HH:MM` Moscow time selection from 4.12.1.
- Manual `📣 Отправить сейчас` flow with confirmation.
- One automatic send per Moscow day after a manual send.
- 4.11.9 AutoScan View Deadlock Recovery.
- Free Radar Preview, funnel analytics, DT Radar, AutoScan 84-category policy, Date/Page/View/AI workers.

## Deploy

No database migration. No new Railway variables. Redeploy **parser service only**.

After deploy, open `Админ-панель → 📨 Daily Radar`. The loading screen should appear immediately. If live metrics fail, the panel will show a warning and Railway logs will contain `Daily Radar metrics failed/timeout`.

---

## Source: `DEPLOY_V4_12_3_DAILY_RADAR_FSM_HOTFIX.md`

# DT PARSER 4.12.3 — Daily Radar FSM Hotfix

Base: **4.12.2 Daily Radar Instant UI**.

## Fixed
- `admin_daily_radar_handler()` used the argument name `fsm: FSMContext`.
- aiogram 3 FSM middleware exposes the context to handlers as `state`, so the callback was invoked without `fsm` and failed with:
  `TypeError: admin_daily_radar_handler() missing 1 required positional argument: 'fsm'`.
- The handler now accepts `state: FSMContext` and clears it normally before opening Daily Radar.
- Internal Daily Radar config in the same handler is renamed to `digest_state` so it cannot shadow the FSM context.

## Preserved
- v4.12.2 instant loading UI and bounded metric reads.
- Manual `📣 Отправить сейчас`.
- Custom `HH:MM` Moscow time.
- Daily scheduler and duplicate-send protection.
- v4.11.9 AutoScan view deadlock recovery.
- DT Radar / Free Radar / funnel analytics.
- Parser/Page/Date/View/AI core unchanged.

## Deploy
Redeploy **parser service only**. No DB migration and no new Railway variables.

## Smoke test
1. Start parser and confirm `version=4.12.3`.
2. Open Admin → `📨 Daily Radar`.
3. The callback must immediately show the loading screen and then the Daily Radar panel.
4. Check custom time and `📣 Отправить сейчас` confirmation.

---

## Source: `DEPLOY_V4_13_0_SIMPLE_REFERRAL_PROMO.md`

# DT PARSER 4.13.0 — Simple Referral Promo

Base: **4.12.3**.

## User flow

1. While the promo is enabled, the main menu shows `🎁 Получить день бесплатно`.
2. The user receives a personal link: `https://t.me/<bot>?start=ref_<telegram_id>`.
3. A referral is counted only if that Telegram user has never existed in `bot_users` before the referred `/start`.
4. Every two promo-eligible referred users atomically add **+1 day** to the referrer's `access_until`.
5. The mechanic repeats without a cap: 2 -> +1 day, 4 -> +2 days, 6 -> +3 days.

## Promo on/off

Admin path: `Админ-панель -> 👥 Рефералы`.

- ON: new attributed users count toward the 2 -> +1 day reward.
- OFF: referral attribution is still stored for analytics, but new entries during the pause are marked non-eligible and never retroactively earn promo days.
- Existing unfinished eligible progress is preserved across pause/resume.

## Integrity

- `referral_invites.referred_user_id` is unique, so one Telegram user can be attributed only once.
- Self-referrals are rejected.
- A referrer must already exist in `bot_users`.
- The two referral rows and the access extension are committed in one DB transaction.
- Existing active access is extended; expired/no access starts from current UTC time.

## Database

A new `referral_invites` table is created automatically with `CREATE TABLE IF NOT EXISTS` before `metadata.create_all` to avoid Railway multi-service first-start races.

No manual migration. No new Railway variables.

## Smoke test

1. Deploy parser and confirm startup logs contain `version=4.13.0`.
2. Open admin -> Referrals; confirm promo status and counters render.
3. Open a user's `🎁 Получить день бесплатно` screen and copy/share the deep link.
4. Start the bot from two Telegram accounts that have never used the bot before.
5. Confirm the referrer screen reaches 2 users and then returns to 0/2 progress with `+1 day` earned.
6. Confirm `access_until` increased by one day.
7. Disable promo; enter from a third brand-new account; confirm overall link entries rise but promo progress does not.

---

## Source: `DEPLOY_V4_14_0_FAST_SOLD_LIFECYCLE.md`

# DT PARSER 4.14.0 — Fast Sold Lifecycle

## Base
Directly based on **4.13.0 Simple Referral Promo**. Referral promo, Daily Radar fixes, AutoScan View Deadlock Recovery, the 84-category AutoScan policy, four foreground parser lanes and existing Page/Date/View/AI workers are preserved.

## What this release adds

### DT Radar — Fast Sold
A new full-access feed appears under `DT Radar -> Лучшие сейчас`:

- `⚡ Fast Sold / Быстро исчезли`
- shows strong listings that DT Radar first saw live and later confirmed unavailable during the first 3 hours;
- each row stores/uses approximate lifetime, disappearance time, price, last known views and Peak DT Score;
- product detail explains that Kleinanzeigen does not always distinguish a completed sale from a seller removing the listing.

### Lifecycle schedule
Only fresh strong Radar listings are watched (`DT Score >= 72`). Checks are anchored to the listing's DT `first_seen_at`:

- +15 min
- +30 min
- +60 min
- +120 min
- +180 min

A single unavailable result becomes only `confirming`. The worker checks the detail page again after ~3 minutes. Only a second unavailable result becomes `disappeared` / Fast Sold.

`403`, `429`, 5xx, timeouts and ambiguous responses are stored as `unknown` and retried gently; they never count as disappearance.

## Architecture

### New `Lifecycle Worker`
The worker is intentionally separate from the Telegram parser:

- no category parsing;
- no Page/Date/View worker queues;
- no browser fallback;
- only lightweight direct detail-page availability checks;
- default concurrency: 4;
- PostgreSQL table `radar_lifecycle_watches` is the durable queue;
- no Redis is required for this worker.

The queue uses row leases and PostgreSQL `FOR UPDATE SKIP LOCKED`, so a worker restart does not lose pending checks and future multiple replicas can avoid claiming the same row concurrently.

## Railway deployment

1. Deploy **4.14.0** to the repository/services as usual.
2. Confirm parser startup contains `version=4.14.0`.
3. Create **one** additional Railway service from the same repo.
4. Name it exactly `Lifecycle Worker`.
5. Give that service the same PostgreSQL `DATABASE_URL` used by Parser.
6. It does **not** need `BOT_TOKEN`. Redis is not required/used by Lifecycle.
7. Keep one replica initially.

`service_launcher.py` will detect the service name and run `lifecycle_worker.py`.

If you use another service name, add:

```text
DT_SERVICE_ROLE=lifecycleworker
```

No other new variables are required. Optional tuning exists (`LIFECYCLE_CONCURRENCY`, `LIFECYCLE_BATCH_SIZE`, `LIFECYCLE_POLL_SECONDS`) but the defaults are the recommended first launch profile.

## Expected logs

Lifecycle service startup:

```text
[service-launcher] version=4.14.0 service='Lifecycle Worker' role=lifecycle-worker target=lifecycle_worker.py ...
DT Radar Lifecycle Worker online | version=4.14.0 concurrency=4 batch=20 poll=10s
```

When Radar finds a strong fresh candidate:

```text
DT Radar Lifecycle queued external_id=... product=... score=... next=...
```

Normal live checkpoint:

```text
Lifecycle check watch=... external_id=... active=True status=watching ...
```

First missing check:

```text
Lifecycle check watch=... external_id=... active=False status=confirming ...
```

Confirmed disappearance about 3 minutes later:

```text
DT Radar Fast Sold confirmed external_id=... product=... lifetime=...s checks=...
Lifecycle check watch=... external_id=... active=False status=disappeared ...
```

## Smoke test

1. Open paid `DT Radar -> Лучшие сейчас` and confirm the new `⚡ Быстро исчезли` button is present.
2. Start/finish an AutoScan category with strong Radar signals.
3. In logs, confirm `DT Radar Lifecycle queued ...` appears for qualifying fresh listings.
4. Confirm Lifecycle Worker stays online and emits heartbeat logs.
5. After due checkpoints, verify `active=True` checks do not affect foreground parser jobs.
6. When a watched ad truly becomes unavailable, verify the first miss is only `confirming` and the second miss records `disappeared`.
7. Open `⚡ Быстро исчезли` and verify the product displays the approximate lifetime and disappearance timestamp.

## Database
`radar_lifecycle_watches` is created automatically with `CREATE TABLE IF NOT EXISTS` before the metadata pass, so simultaneous Railway service startup is safe. No manual migration is required.

---

## Source: `DEPLOY_V4_14_1_RADAR_PRICE_AND_RETURN_UX.md`

# DT PARSER v4.14.1 — Radar Price & Return UX

**Base:** v4.14.0 Fast Sold Lifecycle.

This release changes only DT Radar browsing/search UX. Parser, AutoScan, Date/Page/View/AI/Lifecycle algorithms remain unchanged.

## Added

### 1. Radar price filter
Paid DT Radar users can filter accumulated Radar products by an actually observed listing price.

Available presets:
- any price;
- up to 50 €;
- 50–100 €;
- 100–200 €;
- 200–500 €;
- 500+ €;
- custom range such as `120-250`, `до 100`, or `500+`.

The filter is available in:
- category product feeds;
- Radar text search results.

The selected filter persists while the user continues browsing Radar and is reset by choosing `Любая`.

The DB filter uses `radar_product_listings.last_price_eur`, so a product family is included only when Radar has actually observed at least one listing inside the requested price range. It does not rely only on the broad family min/max envelope.

### 2. Return to the exact Radar list
Opening a Radar product now keeps the current browsing context.

For category browsing the product card shows:
- `⬅️ Назад к категории`

It returns to the same:
- category;
- page;
- sorting mode (`newest` / `best DT Score`);
- active price filter.

Search results return with `⬅️ К результатам`, and ordinary Hot/Rising/AI/Fast Sold/Records/Favorites lists return with `⬅️ К списку`.

Adding/removing a product from `⭐ Мой Radar` no longer destroys this return context.

### 3. Price visibility
Radar category/search result text now shows the observed product price range so the active filter is understandable without opening every product.

## Compatibility
- No PostgreSQL migration.
- No new Railway variables.
- No new worker service.
- `Lifecycle Worker` from v4.14.0 remains fully compatible.
- DT Score / AI Lab formula is intentionally unchanged in this release.

## Deployment
Redeploy the **parser** service with v4.14.1.

If Railway automatically redeploys all services from the same repository, that is safe; auxiliary workers have no behavioral changes in this release.

## Smoke test
1. Open `DT Radar -> Категории -> any subcategory`.
2. Set `Цена -> 100–200 €` and confirm the result count/list changes.
3. Open a product from page 2+, then press `⬅️ Назад к категории`; verify the same page, sort and price filter are restored.
4. Toggle `⭐ Мой Radar` inside the product and verify the same back button remains.
5. Run `DT Radar -> Поиск`, search e.g. `PlayStation`, set a price filter, open a product and return with `⬅️ К результатам`.
6. Set a custom range (`120-250`) and verify it is shown in the filter button/text.

---

## Source: `DEPLOY_V4_15_0_DT_DEMAND_SCORE_2_0.md`

# DT PARSER v4.15.0 — DT Demand Score 2.0

**Base:** v4.14.1 Radar Price & Return UX.

This release replaces the DT AI Lab scoring formula with one demand-focused 0–100 score. It does not use Lifecycle/disappearance as a score input.

## New DT Demand Score formula

The user-facing score remains one number from 0 to 100:

- **40% Relative View Velocity** — age-matched view growth relative to the category, with a small absolute-demand stabilizer;
- **20% Acceleration** — whether current family demand is speeding up versus its own recent history and recent-vs-previous market history;
- **15% Persistence** — whether raw historical view curves keep the family above its category across independent time windows;
- **15% Repeatability** — whether multiple independent listings of the same family repeatedly show strong demand;
- **10% Price Fit** — price versus the observed market median, with the strongest bonus reserved for believable discounts rather than implausibly extreme prices.

Weights total exactly 100%.

## What changed technically

### Relative View Velocity
The old model compared views/hour across a broad category cohort. v4.15.0 prefers a similar-age cohort (very fresh listings are compared with other very fresh listings), then family-balances that cohort so many copies of one product cannot redefine the category baseline.

### Acceleration
Acceleration is based only on demand/view history. Supply and Lifecycle are not part of the score. Missing demand history is neutral instead of receiving a bonus or penalty.

### Persistence
Persistence is derived from raw `view_history` checkpoints across recent and previous market windows. One isolated spike cannot create a high persistence signal. Thin history shrinks toward neutral 0.5 and lowers confidence rather than automatically lowering the product score.

### Repeatability
The score no longer uses old AI `confirmed` labels as historical repeatability evidence. That avoided a circular feedback loop where an older model could indirectly reward its own prior decisions. Historical repeatability now comes from independent raw view-growth observations relative to category demand.

### Price Fit
A realistic discount can improve Score. Extremely low prices no longer automatically get the maximum price bonus because very large deviations can represent damaged/non-comparable/fraudulent listings and are not reliable demand evidence by themselves.

### Follow-up checkpoints
The existing +1h / +3h / +6h AI observation schedule remains unchanged in this release. At each checkpoint the score is recomputed using the same 40/20/15/15/10 structure:

- observed relative velocity;
- live acceleration/momentum;
- persistence across starting/lifetime/latest-interval demand;
- stored repeatability evidence;
- price fit.

The previous `45% initial score + 37% observed strength + 18% momentum` dynamic formula is removed.

## Lifecycle is intentionally separate

Fast Sold / Lifecycle remains available in DT Radar, but confirmed disappearance does **not** add or subtract DT Demand Score points. An ad can disappear because it sold, was manually removed, was moderated, or was fraudulent.

## UI

AI Lab keeps one primary numeric score:

`DT Demand Score: 0–100`

Saturation remains a descriptive diagnostic (`низкая / средняя / высокая`) rather than a second 0–100 score in the AI list/card.

## Compatibility

- No PostgreSQL migration.
- No new Railway variables.
- No new service.
- Existing Date/Page/View/Lifecycle worker algorithms are unchanged.
- Radar price filter and exact return context from v4.14.1 are preserved.
- Referral promo, Daily Radar, AutoScan recovery, Page Cache Recovery and four user scan lanes are preserved.

## Deployment

Redeploy at minimum:

1. **AI Worker** — required for the new scoring model;
2. **Parser** — required for the updated AI Lab wording/version.

Lifecycle Worker, Date Worker, Page Worker and View Worker can run the same repository safely; their algorithms are unchanged.

Expected AI Worker startup/heartbeat should report:

`model_version=dt-demand-score-v2`

## Smoke test

1. Redeploy Parser + AI Worker.
2. Confirm AI Worker heartbeat reports `dt-demand-score-v2`.
3. Complete a normal scan containing fresh listings with exact views.
4. Open `Админ-панель -> DT AI Lab` and confirm candidates use `DT Demand Score` wording.
5. Open a candidate and confirm the reasons include `DT Demand 2.0` with velocity/acceleration/persistence/repeatability diagnostics.
6. Let +1h / +3h / +6h observations run and confirm the Score is recalculated rather than frozen at the initial value.
7. Confirm Lifecycle Worker continues running independently and no disappearance result directly changes DT Demand Score.

---

## Source: `DEPLOY_V4_15_1_DT_DEMAND_SCORE_2_1_EVIDENCE_ADAPTIVE.md`

# DT PARSER v4.15.1 — DT Demand Score 2.1 Evidence Adaptive

**Base:** v4.15.0 DT Demand Score 2.0.

This release fixes the main calibration problem discovered in live Radar rounds: fresh products with exceptional view growth could be mechanically capped near the observation band because future/history factors that did not yet exist were still voting as neutral `0.5` values.

## Formula stays the same

The single user-facing DT Demand Score remains:

- **40% Relative View Velocity**
- **20% Acceleration**
- **15% Persistence**
- **15% Repeatability**
- **10% Price Fit**

Lifecycle/disappearance remains completely outside the score.

## Evidence Adaptive normalization

v4.15.1 changes *when* a factor is allowed to vote.

Unknown evidence is no longer inserted as a synthetic 50% result. Instead, only factors with real evidence are included in the numerator and denominator, and their available base weights are renormalized to 100%.

Example for a genuinely fresh listing:

- Velocity = 95%, available
- Acceleration = unknown
- Persistence = unknown
- Repeatability = unknown
- Price Fit = 80%, available

The score is calculated from the available 40 + 10 base weight only:

`(40×0.95 + 10×0.80) / 50 = 92%`

The missing 50 base-weight points do not push the listing toward 50/100. As +1/+3/+6h checkpoints and more independent listings arrive, acceleration, persistence and repeatability automatically join the same 40/20/15/15/10 model.

## Evidence activation rules

### Velocity
Active when the category/age-matched comparison cohort has at least two observations.

### Acceleration
Initial score: active only when the product has real own/history demand-growth evidence. At follow-up checkpoints it becomes active from measured post-baseline growth.

### Persistence
Initial score: active after at least two independent historical raw demand-rate observations. Follow-up score: active once enough real elapsed time exists to compare the starting and subsequent demand behavior.

### Repeatability
Initial score: active when at least two comparable current listings exist or at least two independent historical raw demand observations exist. Missing repeatability does not qualify a product as a Hidden Gem by itself.

### Price Fit
Active only when the listing has a price and the product family has at least three observed market listings supporting a median. One isolated price cannot move the score.

## Relative Velocity safety gate

Evidence normalization makes Velocity much more important for new products, so v4.15.1 also hardens the Velocity factor against tiny-category false positives.

Relative percentile still dominates, but it is multiplicatively gated by absolute view volume / views-per-hour. A listing with 3 views when its peers have 1 view can no longer become an 88+ signal merely because it ranks first in a tiny cohort. A genuinely strong fresh listing with meaningful absolute traffic can still reach 90+ immediately.

Synthetic regression check used for this release:

- tiny cohort top listing: 3 views / 30 min -> about **73**, not 88+;
- strong fresh outlier: 80 views / 30 min in a normal fresh cohort -> about **94**, even with no future/history evidence yet.

## Follow-up checkpoints

+1h / +3h / +6h remain unchanged as a schedule. They continue to recompute the same single DT Demand Score, now with evidence-adaptive weights.

Observed velocity and acceleration become real evidence immediately. Persistence joins after enough elapsed evidence. Repeatability and Price Fit vote only when their raw evidence is actually present.

## Diagnostics

AI reasons now include:

`DT Demand 2.1: ...`

and an internal Evidence Adaptive line showing which components were actually available and how much of the base 100 weight had real evidence. This is for calibration/debugging; the user-facing product remains one DT Demand Score.

Expected AI Worker model version:

`model_version=dt-demand-score-v2.1-evidence-adaptive`

## Compatibility

- No PostgreSQL migration.
- No new Railway variables.
- No new service.
- No Lifecycle influence on DT Demand Score.
- Radar price filter / return context from 4.14.1 retained.
- Fast Sold Lifecycle from 4.14.0 retained.
- Referral promo, Daily Radar, AutoScan recovery, Page Cache Recovery and four user scan lanes retained.

## Deployment

Redeploy at minimum:

1. **AI Worker** — required for the new scoring behavior;
2. **Parser** — keep the whole deployment on the same v4.15.1 release/version.

Date/Page/View/Lifecycle worker algorithms are unchanged.

## Live validation

Do not judge the release from old 4.15.0 completed candidates alone. Existing AI history is intentionally preserved. Compare new runs whose `AIEarlyWinnerRun.model_version` is `dt-demand-score-v2.1-evidence-adaptive`.

For the next 2–3 Radar circles check:

1. how many initial candidates land in 80–89 and 90+;
2. whether obvious low-volume tiny-category outliers stay below strong-signal levels;
3. whether 80+/90+ products continue to show real view growth at +1/+3/+6;
4. confirmation rate after enough observations finish.

The target is not to maximize the number of 90+ items. The target is to stop suppressing genuinely exceptional fresh demand while keeping weak relative-only noise out.

---

## Source: `DEPLOY_V4_15_2_ORGANIC_DEMAND_INTEGRITY.md`

# DT PARSER v4.15.2 — Organic Demand Integrity

**Base:** v4.15.1 DT Demand Score 2.1 Evidence Adaptive.

This release fixes polluted demand inputs. DT Demand Score remains **40/20/15/15/10** and is intentionally not recalibrated here; v4.15.2 changes which listings are allowed to contribute evidence.

## Organic-only rule

A listing is permanently excluded from demand analytics as soon as DT Parser observes either of these conditions:

1. **Paid Kleinanzeigen visibility**
   - standalone `TOP` badge;
   - `Top-Anzeige` / `TopAd`;
   - `Hochgeschoben` / `Hochschieben` / bump-up;
   - `Highlight`;
   - `Galerie`;
   - explicit sponsored/promoted feature markers.

2. **Reduced / crossed-out price**
   - semantic `<del>` / `<s>` old monetary price;
   - CSS `line-through` old monetary price;
   - explicit old/original/former/previous-price classes/metadata;
   - text such as `statt 199 €`, `vorher ...`, `alter Preis ...`;
   - a real numeric price decrease observed by DT Parser between two stored observations, even if the current card template does not expose the crossed old price.

The flags are **sticky**. If a paid badge later expires or a seller changes the price again, the listing does not become a clean demand sample: its accumulated view counter has already been influenced by non-organic exposure/price intervention.

## Where dirty listings are blocked

Known non-organic listings do not contribute to:

- user scan result lists / TOP ranking;
- fresh exact-view collection;
- future `ViewHistory` demand checkpoints;
- DT AI initial candidates;
- DT AI +1/+3/+6 observations;
- Relative View Velocity cohorts;
- Acceleration / Persistence / Repeatability evidence;
- Price Fit market cohorts;
- DT Radar signals, price statistics and category feeds;
- Lifecycle/Fast Sold enrollment.

The AI Worker also applies defensive DB filters, so historical raw ViewHistory belonging to a now-flagged listing cannot train DT Demand Score.

## Sticky integrity registry

New additive table:

`listing_integrity`

It stores the Kleinanzeigen `external_id` plus sticky `is_promoted` / `is_price_reduced` flags. This is necessary because a TOP card may be rejected before a normal `listings` row is ever created. If the same ad later reappears without the badge, the registry still blocks it from organic analytics.

`listings` also receives additive column:

`is_price_reduced BOOLEAN DEFAULT FALSE`

Both are created automatically by `init_db()`. No manual SQL migration is required.

## Existing Price Drop feature is preserved

Reduced-price listings are excluded from demand scoring but their raw Listing / PriceHistory data may remain stored. The existing `📉 Снижение цены` export can still use that history. Organic Radar/AI and normal scan outputs do not use those rows.

## Cleanup of already polluted Radar / AI history

At parser startup v4.15.2 runs an idempotent Organic Demand cleanup:

- historical numeric downward price steps in `price_history` mark the listing as reduced;
- AI candidates/observations/events for known dirty listings are removed;
- dirty Radar snapshots and Radar listing associations are removed;
- dirty Lifecycle watches are removed;
- affected Radar product families are rebuilt from surviving clean snapshots/listings;
- a product family with no clean evidence left is removed from Radar.

The raw Listing/PriceHistory/ViewHistory audit data is not destructively deleted; analytical queries refuse flagged rows.

Historical paid promotions that were never recorded as `is_promoted` by an older release cannot be reconstructed from the database alone. If such an ad is still shown with a paid marker, the next scan detects it, makes the flag sticky and purges its old analytical contribution immediately.

Expected cleanup log when applicable:

`Organic Demand cleanup dirty=... ai=... radar_snapshots=... radar_links=... lifecycle=...`

## Cache integrity

v4.15.2 changes result-card filtering semantics, so old page caches must not be replayed:

- Redis Page Worker cache uses schema `v4152-organic`;
- stable PostgreSQL page-checkpoint payloads use schema `v4152-organic` and old payloads are rejected.

No manual Redis clear is required.

## Parser telemetry

Category logs now report both exclusions separately:

- `promoted=...`
- `reduced_price=...`

The standalone `TOP` badge is detected conservatively as a badge/label; ordinary listing titles such as `TOP Zustand Fahrrad` are not rejected merely because they contain the word TOP.

## DT Demand Score is unchanged

v4.15.2 retains v4.15.1 exactly:

- 40% Relative View Velocity
- 20% Acceleration
- 15% Persistence
- 15% Repeatability
- 10% Price Fit
- evidence-adaptive normalization
- Lifecycle/disappearance outside the score

This release improves the **truth of the inputs**, not the weights.

## Railway deployment

Redeploy these services from the same v4.15.2 checkout:

1. **Parser** — DB migration, sticky registry, startup cleanup and scan integrity;
2. **Page Worker** — new TOP/reduced-price parsing and cache schema;
3. **Date Worker** — uses the shared category-page parser and should stay on the same parsing semantics;
4. **AI Worker** — organic-only training/candidate/checkpoint queries;
5. **Lifecycle Worker** — receives the defensive non-organic race guard from `radar.py`.

The **View Worker algorithm is unchanged**. Redeploying it from the same repository is safe but not required for behavior.

No new Railway variable is required. Do not copy main-parser stable-mode variables to helper workers.

## Smoke test

1. Open/scan a category containing a paid standalone `TOP` card. Logs should increase `promoted=...`; that external ID must not appear in the scan result or Radar.
2. Use a normal title containing the word `TOP` but no paid badge. It must remain eligible.
3. Find a card with a crossed old price. Logs should increase `reduced_price=...`; the listing must not receive exact views or enter Radar/AI.
4. Re-scan a previously flagged external ID after its badge/crossed price disappears. It must remain excluded via `listing_integrity`.
5. Check AI Worker after new scans: candidates and market cohorts must contain only `is_promoted=FALSE AND is_price_reduced=FALSE` listings.
6. Check Radar after startup cleanup: previously known dirty snapshots should disappear/rebuild while clean product-family history remains.
7. Verify `📉 Снижение цены` still works from stored price history.

---

## Source: `DEPLOY_V4_15_3_STRICT_ORGANIC_RADAR_GATE.md`

# DT PARSER v4.15.3 — Strict Organic Radar Gate

**Base:** v4.15.2 Organic Demand Integrity.

v4.15.3 closes the remaining cross-process admission race around DT Radar. The parser, AI worker and Lifecycle worker can run in different Railway processes, so a stale ORM `Listing` object must never be enough to admit a Radar signal after another process has already marked the same Kleinanzeigen ad as promoted or price-reduced.

The DT Demand Score formula remains **40 / 20 / 15 / 15 / 10**. Parser detection semantics from v4.15.2 remain unchanged. This is a Radar integrity hardening release.

## Strict DB-authoritative Radar admission

Every new Radar signal now passes one central gate inside the same transaction that would create the `RadarSnapshot`:

1. the current `listings` row must exist;
2. `listings.is_promoted` must be exactly `FALSE`;
3. `listings.is_price_reduced` must be exactly `FALSE`;
4. `listing_integrity` must have no sticky promoted/reduced flag for the external ID.

The gate does **not** trust only the `Listing` object passed by Main Bot / AI Worker. It re-reads PostgreSQL immediately before Radar admission.

Blocked writes log:

`Strict Organic Radar Gate blocked source=... external_id=... reason=...`

Typical reasons are `listing_promoted`, `listing_price_reduced`, `sticky_registry` and `listing_missing`.

## Cross-process race lock

Radar admission and parser-side integrity writes now share the PostgreSQL advisory-lock namespace:

`organic-integrity:<external_id>`

This gives deterministic ordering:

- if Radar admission commits first, a later promotion/reduction mark immediately runs the existing idempotent purge and removes that signal;
- if the promotion/reduction mark commits first, Radar waits for the same lock and then rejects the signal from current DB state.

Page persistence also takes the same per-listing integrity locks, so a numeric price drop discovered while persisting a page cannot race a Radar write.

No new Redis lock or Railway variable is required.

## Lifecycle uses the same gate

Fast Sold / Lifecycle writes a disappearance snapshot directly rather than calling the normal Radar upsert path. v4.15.3 therefore applies the same DB-authoritative gate before a leased Lifecycle check can create a Fast Sold signal.

If the listing is dirty (including registry-only contamination), the watch becomes `excluded` and cannot create a disappearance signal.

## Read gate — dirty evidence is invisible immediately

User-facing Radar reads no longer depend on cleanup having completed a few milliseconds earlier. A Radar product is visible only when it has at least one association **and every currently linked association is DB-confirmed clean**. If even one linked external ID becomes dirty/unverified, the whole family is hidden for the short interval until purge/rebuild removes that association; this prevents a contaminated aggregate score from flashing in the UI.

The strict read gate covers:

- Radar totals / Hot / Rising / AI Picks counters;
- category feeds and counts;
- Radar search;
- price-filter matching (dirty listing prices cannot satisfy a filter);
- product detail selection and displayed snapshots;
- Fast Sold feeds / lookup;
- Lifecycle job claiming.

This means a sticky registry flag hides contaminated Radar evidence immediately even if a stale aggregate row still exists until cleanup finishes.

## Registry-authoritative cleanup repair

v4.15.2 cleanup primarily discovered dirty IDs from `listings` flags. v4.15.3 treats both tables as authoritative contamination sources:

- dirty IDs are the union of `listings` flags and `listing_integrity` flags;
- if the registry is dirty while the Listing row still says clean, cleanup repairs the Listing flags to the sticky OR of both sources;
- if the Listing is dirty but the registry is missing/incomplete, cleanup repairs the registry;
- AI/Radar/Lifecycle analytical contributions for the dirty external ID are then purged as before;
- affected Radar families are rebuilt or removed from surviving clean evidence.

This repair is idempotent and runs at Parser startup through the existing Organic Demand cleanup.

## What is intentionally unchanged

- DT Demand Score: **40% Relative View Velocity / 20% Acceleration / 15% Persistence / 15% Repeatability / 10% Price Fit**;
- v4.15.2 TOP / Top-Anzeige / Hochgeschoben / Highlight / Galerie / sponsored detection;
- v4.15.2 crossed-price / numeric price-drop detection;
- Page / Date / View worker algorithms;
- four user scan lanes and FIFO queue;
- AutoScan category/page policy;
- subscription/referral/Daily Radar UX;
- database schema — no new table/column;
- Redis schema — no new cache migration;
- Railway variables — none added.

## Railway deployment

Redeploy from the same v4.15.3 checkout:

1. **Parser** — required (shared integrity locks, startup repair, Radar reads/writes);
2. **AI Worker** — recommended/required for one-version consistency because it calls the hardened Radar writer;
3. **Lifecycle Worker** — required for the strict Lifecycle gate;
4. **Page Worker / Date Worker** — parser semantics are unchanged from v4.15.2, but redeploy from the same commit when using one-repo Railway deploys.

**View Worker algorithm is unchanged.** Redeploying it from the same repository is safe but not behaviorally required.

No manual SQL and no Redis clear are required.

## Smoke test

1. Scan a normal organic listing and confirm it can enter Radar.
2. Add/observe a paid `TOP` / `Hochgeschoben` marker for that external ID. Confirm the existing v4.15.2 purge removes its Radar contribution.
3. Simulate stale Listing state: keep `listings.is_promoted=FALSE` but set `listing_integrity.is_promoted=TRUE`. Attempt a Radar write. It must log `reason=sticky_registry` and create no snapshot.
4. Before running cleanup, open Radar feeds/search/categories. A product backed only by that dirty external ID must already be invisible through the read gate.
5. Run/start Parser cleanup. Confirm the Listing flag is repaired to `TRUE` from the registry and stale Radar/AI/Lifecycle evidence is removed.
6. Repeat with `is_price_reduced=TRUE` in the registry.
7. Lease a Lifecycle watch, then dirty the external ID before completion. Completion must return/expose `excluded` and must not create `lifecycle-fast:*` snapshot.
8. Verify a normal listing whose title merely contains `TOP` but has no paid badge remains eligible.
9. Verify `📉 Снижение цены` still has access to raw PriceHistory while the reduced listing remains excluded from Radar demand analytics.

---

## Source: `DEPLOY_V4_15_4_ORGANIC_PIPELINE_CORRECTNESS.md`

# DT PARSER v4.15.4 — Organic Pipeline Correctness

**Base:** v4.15.3 Strict Organic Radar Gate.

v4.15.4 fixes the full AutoScan → exact views → Organic Gate → Radar pipeline after live telemetry showed a healthy page crawl producing almost no Radar signals. The Organic policy is **not loosened**: paid visibility and reduced-price listings remain excluded. The release fixes the places where clean listings were silently lost before Radar and completes the strict detail-page admission contract.

## 1. Root cause fixed: no 24-item exact-view recovery cap

Older AutoScan behavior tried the cheap official visit counter for all clean listings, but only a bounded 24 unresolved URLs were sent through the exact recovery path. Every other miss was written as `view_count = NULL`, and Radar later selected only rows with non-null views. A category could therefore be marked successful while almost all clean listings were invisible to Radar.

v4.15.4 now:

1. tries the cheap official counter for every clean target-date listing;
2. sends **every unresolved URL** through the dedicated exact View Worker/browser recovery path;
3. persists only verified exact counters;
4. if even one target remains unknown, marks that category `⚠️ допроверка` for Radar and does **not** rank an incomplete view population.

When Redis is configured, the dedicated View Worker fleet is enabled by default unless `REMOTE_VIEW_WORKER_ENABLED=0` is explicitly set. If the fleet is expected but has no live heartbeat, AutoScan does not open hundreds of heavy local fallbacks inside the main parser: unresolved views remain unknown and the category is retried later.

This is correctness-first: an unknown listing could be the real TOP-1, so an incomplete population must not be presented as an exact ranking.

## 2. Real live detail-page Organic Gate

Search-card filtering remains the cheap first line. Before a candidate can enter Radar, v4.15.4 also opens the exact public `/s-anzeige/...` detail page and proves:

- final canonical listing ID matches the requested `external_id`;
- no paid `TOP` / `Top-Anzeige` marker;
- no bump/`Hochgeschoben` paid marker;
- no paid Highlight / Galerie feature marker;
- no sponsored/promoted marker;
- no crossed/old/reduced price UI.

Normal wording such as `TOP Zustand` does not count as a paid badge. A normal photo-gallery control labeled `Galerie` also does not count as paid Galerie without a paid-feature class/data marker.

`403`, `429`, challenge HTML, wrong redirect, wrong listing identity, unavailable/weak document and transport failures are **UNKNOWN**, never organic.

## 3. Sticky dirty verdict stays strict

A detail page that proves paid visibility or a reduced price immediately updates both:

- `listings.is_promoted` / `listings.is_price_reduced`;
- sticky `listing_integrity`.

The affected listing is then purged from AI/Radar/Lifecycle analytics and the affected Radar family is rebuilt from surviving clean evidence. Raw Listing / PriceHistory / ViewHistory audit history remains available.

## 4. Correct Organic TOP-N backfill

The old implementation could stop after the raw first 12 candidates. v4.15.4 walks the exact-view ranking in order:

- proven promoted/reduced candidates are skipped;
- the next ranked candidate is checked;
- this continues until up to **12 verified organic** positions are admitted or the ranked list ends.

There is no artificial `rows[:12]` or 24/48 correctness cutoff.

If a higher-ranked candidate gets an **UNKNOWN** detail verdict, the pipeline stops fail-closed and the category goes to `⚠️ допроверка`. It does **not** fill lower positions past an unknown candidate, because that candidate may actually belong in the Organic TOP-12.

## 5. Retry idempotency

A retry chain reuses the parent AutoScan round ID for Radar source keys. Radar snapshots already committed before a later transient UNKNOWN are treated as existing idempotent successes, not new repeatability evidence. A brand-new manual/daily round still creates fresh observations.

Admin telemetry separates:

- new Radar signals;
- signals already present from the parent retry round.

## 6. Legacy Radar quarantine is now real

New additive column:

`radar_products.organic_verified_at TIMESTAMP NULL`

All families created before v4.15.4 begin unverified (`NULL`) and are hidden from user-facing Radar lists/search/categories/counters until a new strict live-detail signal certifies them.

On the first strict certification of a legacy family, pre-gate Radar snapshots, associations and Lifecycle watches for that family are reset, while the stable product ID/favorites remain. The family is rebuilt from strict v4.15.4 evidence.

This is intentional: old data is not automatically trusted merely because it existed before the new gate.

## 7. Transparent AutoScan funnel

The admin AutoScan screen now shows the hidden stages that previously looked like `8369 listings → +3 Radar`:

- clean target-date listings;
- search-card TOP/Promo exclusions;
- search-card / historical price-reduction exclusions;
- exact views verified / requested;
- exact-view Radar candidates;
- detail pages checked;
- Organic passed;
- detail TOP/Promo blocked;
- detail reduced-price blocked;
- detail UNKNOWN;
- new Radar signals;
- idempotent already-present signals during retry.

A category with incomplete views or an unknown higher-ranked detail candidate is no longer counted as `✅ Успешно` for Radar.

## 8. What does NOT change

DT Demand Score remains exactly:

- **40% Relative View Velocity**
- **20% Acceleration**
- **15% Persistence**
- **15% Repeatability**
- **10% Price Fit**

Evidence-Adaptive normalization remains unchanged. Lifecycle/Fast Sold remains outside DT Demand Score. Fast Sold still requires a strong fresh Radar signal and confirmed disappearance logic; it simply receives cleaner, more complete Radar input after this fix.

Page/date chronology, the private-seller search filter, four user scan lanes and FIFO queue remain unchanged.

## 9. Database / Railway

`init_db()` adds `radar_products.organic_verified_at` automatically. No manual SQL is required.

No new Railway variable is required.

Behavior-critical deploy from the **same v4.15.4 checkout**:

1. **Parser** — required: full AutoScan pipeline, telemetry, DB migration, Radar admission;
2. **all View Worker replicas** — strongly required for complete exact-view recovery under load;
3. **AI Worker** — required so AI → Radar uses the same live Organic Gate;
4. **Lifecycle Worker** — required/recommended for one strict Radar/Lifecycle version.

Page Worker and Date Worker parsing algorithms are unchanged from v4.15.3, but deploying them from the same checkout is recommended for one-version consistency.

### Important for an in-progress v4.15.3 circle

Do **not** continue an old v4.15.3 circle after deploying this release. Stop the current circle, deploy v4.15.4, then start a **new full AutoScan round**. The old round already processed categories with the incomplete view-recovery semantics and its aggregate counters cannot be retroactively made trustworthy.

## 10. Smoke test

After deploy, run a fresh AutoScan and verify:

1. For every successful category, `Точные просмотры` reaches `verified/requested`. If it does not, that category must appear under `⚠️ допроверка` and must not claim a complete Radar ranking.
2. `Чистых объявлений даты` remains much larger than the Organic TOP candidates; it is the clean input population, not the number that should enter Radar.
3. A normal clean detail page passes and the category fills up to 12 organic positions when enough candidates exist.
4. A paid TOP candidate is stickily blocked and the next proven candidate backfills its slot.
5. A crossed old price is stickily blocked and the next proven candidate backfills its slot.
6. `TOP Zustand ...` without a paid badge remains eligible.
7. A normal `Galerie` photo-control does not trigger paid Galerie by text alone.
8. A wrong detail redirect or page containing the requested ID only in recommendations is rejected as `wrong_identity`.
9. A 403/429/challenge on a higher-ranked candidate makes the category `⚠️ допроверка`; lower candidates are not silently promoted past it.
10. Repeating only failed categories does not create duplicate repeatability signals for candidates already committed in the parent round.
11. Legacy pre-v4.15.4 Radar families remain hidden until a new strict signal certifies them.
12. DT Demand Score model/weights remain 40/20/15/15/10.

Expected healthy production pattern is no longer a mysterious `thousands → 3`. The exact counts depend on category demand and true non-organic evidence, but every reduction stage is now visible and fail-closed.

---

## Source: `DEPLOY_V4_15_5_AUTOSCAN_RECOVERY_HARDENING.md`

# DT PARSER v4.15.5 — AutoScan Recovery Hardening

**Base:** v4.15.4 Organic Pipeline Correctness.

This release fixes the two remaining causes of repeat-round churn observed after the v4.15.4 funnel became transparent. It does **not** relax Organic admission and does **not** change DT Demand Score.

## 1. Date/Page UNKNOWN recovery

- `stable_fetch()` now gives persistent chronology `unknown` the same one-time fresh BrowserContext recovery previously reserved for `invalid`.
- The already-existing deterministic `sequential_locator()` is now actually invoked when exponential/binary date probes or the boundary neighborhood remain weak.
- Sequential recovery starts after the last proven `newer` page when possible, so it avoids needless page-1 rewinds.
- It remains fail-closed: if a page is still weak after retries + context recycle, the sequential pass does not claim an absent date across that gap.
- Remote Date Worker hints remain acceleration only; local verified chronology is still authoritative.

## 2. Detail Organic UNKNOWN recovery

Before a strong candidate can make an otherwise healthy category `⚠️ допроверка`, the exact detail URL now gets:

1. bounded normal HTTP attempts;
2. short retry delay for transient refusal/transport/weak/challenge responses;
3. one fresh rendered Chromium detail fetch;
4. if still UNKNOWN, one delayed final retry of **that exact candidate only**.

No lower-ranked candidate is promoted past a persistent UNKNOWN. The gate is still correctness-first and fail-closed. Proven TOP/Promo and reduced-price ads remain sticky exclusions.

## 3. UNKNOWN reason telemetry

AutoScan now aggregates exact detail UNKNOWN reasons and shows them under the funnel, for example:

`↳ причины: http_403 3 · challenge 2 · weak_document 1`

The completion notification and failed-category reason include the same breakdown. This distinguishes site pressure from parser/template problems immediately.

## 4. What stays unchanged

- Organic policy: paid visibility and reduced price are excluded.
- Exact-view completeness requirement from v4.15.4.
- Organic TOP-N backfill and fail-closed ranking.
- DT Demand Score **40 / 20 / 15 / 15 / 10**.
- Fast Sold/Lifecycle logic.
- Four user scan lanes and FIFO queue.
- Page depth: 15 per AutoScan category maximum.

## 5. Deployment

Behavior-critical: redeploy **Parser** from v4.15.5.

Recommended same-checkout consistency:
- AI Worker
- Lifecycle Worker
- Date Worker / Page Worker / View Worker

There is no manual SQL migration and no new required Railway variable. Optional tuning variables have safe defaults:

- `DETAIL_INTEGRITY_HTTP_RETRIES=2`
- `DETAIL_INTEGRITY_RETRY_DELAY_SECONDS=0.45`
- `DETAIL_INTEGRITY_BROWSER_FALLBACK=1`
- `DETAIL_INTEGRITY_BROWSER_SETTLE_MS=180`

## 6. Smoke test

1. Run a new AutoScan or retry the current failed set.
2. Confirm transient date `unknown` logs can show `Date sequential recovery ...` and either recover to `found/absent/too_deep` or remain honestly `unknown`.
3. Confirm a persistent weak date page gets one fresh BrowserContext attempt before review.
4. Confirm detail transient failures show HTTP/browser recovery logs and do not immediately fail the category.
5. Confirm a persistent detail UNKNOWN still stops ranking fail-closed.
6. Confirm admin telemetry shows exact UNKNOWN reasons when any remain.
7. Confirm Organic passed = new Radar + already-present for admitted candidates.
8. Confirm exact views remain complete for every successful category.

---

## Source: `DEPLOY_V4_15_6_BUMP_RESURRECTION_INTEGRITY.md`

# DT PARSER v4.15.6 — Bump Resurrection Integrity

**Base:** v4.15.5 AutoScan Recovery Hardening.

v4.15.6 closes the remaining paid-visibility hole shown by live Kleinanzeigen ads that carry the purple circular up-arrow (`Hochschieben`) but no text badge that older detection recognized. It also prevents an already-known external ID from re-entering Organic Radar after its displayed publication day jumps forward.

## 1. Purple Hochschieben icon detection

Search-card and live detail-page parsing now inspect semantic SVG/icon attributes in addition to visible text/classes:

- `bumpup` / `bump-up`;
- `hochschieb*`;
- `push-up` / `pushup`;
- paid feature/boost tokens;
- up-arrow icon tokens only when they sit inside an explicit promotion/visibility feature context.

A generic navigation `arrow-up` is **not** enough to classify an ad as promoted. `TOP Zustand` in a title is still allowed.

## 2. Same-external-ID resurrection detection

`listings.first_posted_date_msk` stores the earliest publication day DT can defend for an external ID and never moves forward.

If the same external ID later appears with a newer `posted_date_msk`, or DT has a database `first_seen_at` earlier than the newly claimed publication day, the chronology is impossible for a genuinely new listing. The ad becomes sticky promoted with reason:

- `resurfaced_posted_date_shift`, or
- `resurfaced_after_first_seen`.

This does **not** use a high view count as proof. A genuinely viral fresh listing is not rejected merely for having many views.

## 3. Sticky reason registry

`listing_integrity` receives additive `promotion_reason VARCHAR(80)`.

Examples:

- `search_promotion_marker`;
- `bump_icon`;
- `promoted_dom_marker`;
- `promoted_metadata`;
- `resurfaced_posted_date_shift`;
- `resurfaced_after_first_seen`.

Once promoted, the external ID stays excluded from Organic Demand even after the paid marker disappears.

## 4. Existing Radar is cleaned, not grandfathered

On the first Parser startup after 4.15.6:

1. all currently visible Radar families are temporarily quarantined (`organic_verified_at = NULL`);
2. Telegram startup is not blocked by the network sweep;
3. Radar maintenance re-checks every current Radar association with the new live detail gate plus resurrection chronology;
4. a proven paid/reduced listing is stickily marked and the existing targeted purge removes its Radar snapshots, product association, AI candidate/observations and Lifecycle/Fast Sold watch;
5. clean historical families are marked `bump_sweep_verified_at` after their old associations pass the sweep, but **remain quarantined**;
6. a fresh v4.15.6 AutoScan/user/AI signal resets the family's pre-v4.15.6 snapshots/links and rebuilds it from demand-safe evidence before `organic_verified_at` is restored;
7. an UNKNOWN detail result stays quarantined instead of being guessed organic and is retried by maintenance.

The sweep is idempotent and persisted with AppSetting flags.

## 5. Organic baseline for unknown pre-DT history

New additive Listing fields:

- `organic_baseline_views`;
- `organic_baseline_at`;
- `organic_history_status`;
- `first_posted_date_msk`.

Radar also receives `bump_sweep_verified_at`, which records completion of the one-time historical bump check **without** making legacy score/history visible.

Raw counters remain stored unchanged for audit/UI. Demand ranking uses two modes:

- a clean listing first observed on its displayed publication day may use its fresh verified total after the bump gate;
- an older/ambiguous listing gets a baseline first; its first inherited total is not used as Organic Radar/AI velocity. After a second clean observation, demand uses only the DT-observed delta above that baseline.

This is intentionally different from `many views = promoted`: view volume alone never creates a sticky promotion flag.

## 6. Cache integrity

Because card promotion semantics changed, stale parsed page payloads must not be replayed:

- Redis Page Worker schema: `v4156-bump-resurrection`;
- stable PostgreSQL page payload schema: `v4156-bump-resurrection`.

No manual Redis clear is required.

## 7. Existing Organic rules remain

Still excluded:

- TOP / Top-Anzeige;
- Hochschieben;
- paid Highlight;
- paid Galerie;
- sponsored/promoted visibility;
- crossed/reduced-price listings.

DT Demand Score weights remain **40 / 20 / 15 / 15 / 10**. Fast Sold remains outside the score.

## 8. Database migration

`init_db()` adds all columns automatically. No manual SQL is required.

No new Railway variable is required.

## 9. Railway deployment

Redeploy from the **same v4.15.6 checkout**:

1. **Parser** — required: DB migration, resurrection detection, quarantine + historical Radar sweep;
2. **Page Worker** — required: new icon detector + cache schema;
3. **Date Worker** — required/recommended: shares card parsing and stable payload schema;
4. **AI Worker** — required: Organic Baseline-aware initial velocity;
5. **Lifecycle Worker** — required/recommended for one strict integrity version;
6. **View Worker** — algorithm unchanged; same-checkout deploy recommended for version consistency.

## 10. Expected first-deploy behavior

Immediately after Parser starts, the old Radar may temporarily show fewer or zero families because the pre-4.15.6 base is quarantined before the network sweep. This is intentional correctness-first behavior.

Logs include:

`v4.15.6 quarantined existing Radar pending bump-resurrection integrity sweep`

and then:

`v4.15.6 bump-resurrection Radar sweep: {'products': ..., 'checked': ..., 'clean': ..., 'dirty': ..., 'unknown': ..., 'sweep_verified': ...}`

Dirty families disappear permanently from Organic Radar. Sweep-clean legacy families remain hidden until a **fresh v4.15.6 signal** rebuilds them; this prevents old accumulated totals from being silently trusted again. The sweep runs at background traffic priority and later retries only families whose historical sweep is still unresolved.

## 11. Smoke tests

1. A card/detail containing an SVG/use token such as `icon-feature-bumpup` must be promoted.
2. A generic navigation `icon-arrow-up` outside a promotion context must remain clean.
3. `TOP Zustand Fahrrad` without a paid badge must remain clean.
4. Same external ID with `first_posted_date_msk=2026-08-13` and current `posted_date_msk=2026-08-29` must become sticky promoted.
5. A current Radar listing proven bumped must lose its Radar snapshots/link and Lifecycle watch.
6. A clean legacy family must get `bump_sweep_verified_at` but keep `organic_verified_at=NULL` until a fresh strict signal arrives.
7. That fresh strict signal must reset pre-v4.15.6 snapshots/links and then restore `organic_verified_at`.
8. An UNKNOWN sweep result must remain quarantined, not organic.
9. A history-unknown listing gets one baseline; only a later observed delta may train velocity.
10. Exact-view completeness and v4.15.5 Date/Detail recovery remain unchanged.
11. DT Demand Score weights remain 40/20/15/15/10.

---

## Source: `DEPLOY_V4_15_7_VERIFIED_ORGANIC_VELOCITY.md`

# DT PARSER v4.15.7 — Verified Organic Velocity

**Base:** v4.15.6 Bump Resurrection Integrity.

v4.15.7 removes the last way an inherited/unknown view total can dominate DT Radar or DT Demand Score. A large counter is **not** treated as proof of paid promotion. Instead, DT separates the counter it inherited before it started observing the ad from growth it personally verified afterwards.

DT Demand Score weights stay exactly **40 / 20 / 15 / 15 / 10**.

## 1. Hard first-observation rule: 400+

The threshold is fixed in code at **400 views** and applies only to the **first exact counter DT observes for that external ID**.

- first DT observation `0..399` on a genuinely fresh clean listing may continue using the normal fresh-total logic;
- first DT observation `>=400` becomes an **untrusted Organic Baseline**;
- the inherited baseline contributes **zero** views to Radar ranking and **zero** Relative View Velocity to DT Demand Score;
- high view count alone never sets `is_promoted` and never creates a sticky promotion verdict.

Examples:

- `399 -> 520`: the listing was first seen below the threshold; its later crossing of 400 does not invalidate already observed demand;
- first seen at `400`: baseline only;
- first seen at `942`: baseline only;
- first seen at `16,337`: baseline only.

This prevents a suspected old/bumped/reposted ad from beating a genuine fresh winner merely because it already carried a large counter when DT discovered it.

## 2. Two clean checkpoints are mandatory for 400+ baselines

A 400+ baseline moves through:

1. `high_baseline` — inherited total stored, no Score vote;
2. after the first later clean exact measurement (minimum 30 minutes later): `high_check_1` — still no Score vote;
3. after the second later clean exact measurement (another minimum 30 minutes later): `observed` — the listing is certified for **delta-only** demand scoring.

Same-batch retries or measurements less than 30 minutes apart cannot masquerade as separate checkpoints. A counter rollback also does not count as clean growth evidence.

Once certified:

`demand_views = current_exact_views - organic_baseline_views`

and elapsed time is measured from `organic_baseline_at`, not from the inherited publication total.

Example:

- 11:00 baseline: `942` -> contributes `0`;
- 11:30 checkpoint #1: `1002` -> still contributes `0`;
- 12:00 checkpoint #2: `1077` -> certified delta `+135` over 60 minutes;
- DT evaluates `+135 / hour`, never `1077 / hour`.

## 3. Automatic low-priority verification

Parser runs a lightweight **Verified Organic Velocity scheduler** for pending 400+ baselines.

- user scans always have priority;
- before **each** checkpoint the exact listing detail page must pass the live Organic Gate again;
- only then does the checkpoint use the existing exact View Worker/browser recovery path;
- only recently seen active listings (last seen within 24h) are auto-checked, so old library rows cannot create a background traffic storm;
- pending ads are checked in small batches;
- a failed counter request is retried later instead of being converted into organic evidence;
- no separate Railway service is required.

This avoids the dead end where a 400+ ad is withheld correctly but never receives the later measurements needed to prove a genuine hit.

## 4. Verified winners can re-enter Radar

After checkpoint #2, DT does not blindly restore the old total.

The newly verified listing is placed into a category/age-matched cohort built only from **demand-safe** view metrics. `score_initial_rows()` calculates the existing evidence-adaptive DT Demand Score from that verified delta. A dedicated `verified_velocity` Radar signal is emitted only when the resulting Score is at least **72**.

Therefore:

- an inherited `16,337` with weak later growth stays out;
- an inherited `942` that then genuinely gains `+135/hour` can still surface;
- all scoring uses observed delta, not the inherited total.

## 5. Defensive gates across Radar and AI

`demand_safe_metric()` is now the shared authority for view provenance.

Radar scan TOP, AutoScan TOP and AI initial candidate scoring all refuse a pending 400+ baseline. `record_ai_candidate()` also defensively rejects an old/stale AI candidate if its Listing is currently high-baseline pending.

Historical ViewHistory demand cohorts only use listings whose latest provenance state is `trusted`/`observed`; pending high baselines cannot quietly influence Persistence/Repeatability market baselines before certification.

## 6. Cleanup of 4.15.6 high-total scores

The first Parser startup on v4.15.7 performs an idempotent repair for clean listings whose stored first Organic Baseline is already `>=400`:

- their provenance is reset to `high_baseline` with **0** certified checkpoints;
- old AI candidates/observations based on inherited totals are removed;
- their Lifecycle/Fast Sold watches are removed;
- affected Radar product families are quarantined with `organic_verified_at = NULL`.

The listing is **not** marked promoted merely because it had 400+ views.

When a later clean demand-safe signal reaches an affected Radar family, the existing strict family-reset path deletes the old snapshots/associations and rebuilds the family from current verified evidence. This prevents pre-v4.15.7 high-total snapshots from silently becoming visible again.

## 7. AutoScan telemetry

The AutoScan admin screen now distinguishes:

- exact counters collected;
- `Initial >=400` listings waiting for two checkpoints;
- high-baseline listings whose delta is already verified;
- demand-safe candidates actually eligible for Radar ranking;
- normal detail Organic Gate results.

Typical line:

`Initial >=400: 17 ждут 2 замера · delta verified: 4`

Those 17 are not lost and are not called promoted; they are simply prevented from influencing Score until DT has direct evidence.

## 8. Database migration

`listings` receives additive fields automatically:

- `organic_verified_checkpoints INTEGER DEFAULT 0`;
- `organic_last_checkpoint_at TIMESTAMP`;
- `organic_last_checkpoint_views INTEGER`.

Existing v4.15.6 fields remain:

- `organic_baseline_views`;
- `organic_baseline_at`;
- `organic_history_status`.

No manual SQL migration is required.

No new required Railway variable is required.

## 9. What does NOT change

- Organic paid-visibility detection from v4.15.6 (purple Hochschieben icon, TOP, Highlight, paid Galerie, sponsored markers);
- same-external-ID resurrection detection;
- sticky promotion/reduced-price integrity registry;
- exact view-count acceptance rules;
- Date/Page chronology and v4.15.5 recovery;
- four foreground user scan lanes/FIFO;
- DT Demand Score weights: **40% Relative View Velocity / 20% Acceleration / 15% Persistence / 15% Repeatability / 10% Price Fit**;
- Lifecycle remains outside DT Demand Score.

## 10. Railway deployment

Behavior-critical deploy from the **same v4.15.7 checkout**:

1. **Parser** — required: DB migration, startup cleanup, 400+ scheduler, Radar telemetry;
2. **AI Worker** — required: shared demand-safe initial scoring and historical cohort filtering;
3. **Lifecycle Worker** — recommended/required for one-version Radar/Lifecycle model;
4. **Page Worker + Date Worker** — parsing semantics are unchanged from v4.15.6; same-checkout deployment is recommended for version consistency;
5. **View Worker** — algorithm unchanged, but it supplies the exact low-priority checkpoints and should remain healthy.

A fresh full AutoScan after deployment is recommended. Existing affected Radar families are quarantined automatically, so old high-total scores do not need a manual database delete.

## 11. Smoke tests

1. First exact counter `399` on a same-day clean listing -> demand-safe total may be used normally.
2. The same listing later reaches `500` -> it is **not** reclassified merely for crossing 400 later.
3. First exact counter `400` -> `high_baseline`, 0 checkpoints, no Radar/AI view score.
4. First exact counter `942` -> same behaviour.
5. First follow-up at +30m must first pass the live detail Organic Gate; then checkpoint=1, still no demand metric.
6. Second follow-up at +60m must pass the live detail Organic Gate again; then checkpoint=2, status `observed`, metric is `current - baseline` only.
7. Two retries within a few minutes must not count as two checkpoints.
8. A counter decrease must not certify organic velocity.
9. A verified high-baseline listing with weak delta must stay out of `verified_velocity` Radar signal (Score <72).
10. A verified high-baseline listing with genuinely strong category-relative delta may enter Radar, with reasons explicitly showing baseline/delta.
11. Pre-v4.15.7 AI candidates and Lifecycle watches tied to clean 400+ baselines are removed on first startup; affected Radar families are quarantined.
12. TOP/Hochschieben/reduced listings stay sticky-excluded exactly as before.
13. DT Demand Score remains 40/20/15/15/10.

---

## Source: `DEPLOY_V4_15_8_AUTOSCAN_DEADLOCK_HARD_STOP.md`

# DT PARSER v4.15.8 — AutoScan Deadlock & Hard Stop

**Base:** v4.15.7 Verified Organic Velocity.

v4.15.8 is a runtime-correctness release for DT Radar AutoScan. It does **not** change Organic Demand rules, the 400+ baseline rule, or DT Demand Score weights.

## 1. Root cause fixed: background Detail Gate lock inversion

v4.15.7 used one process-global Radar detail lock/parser for both:

- foreground AutoScan / Radar admission;
- background Bump Resurrection sweep / Verified Organic Velocity checks.

With `TRAFFIC_BACKGROUND_VIEWS_DURING_SCANS=0`, a race was possible:

1. background work acquired the shared detail lock;
2. AutoScan started a category and registered a foreground scan job;
3. the background task then waited for a background view lease that is intentionally blocked during scans;
4. AutoScan later reached Organic Detail Gate and waited for the lock held by that background task.

That is a real lock inversion and explains a round remaining at `0/84` indefinitely.

v4.15.8 splits detail verification into two isolated lanes:

- foreground detail lock + parser;
- background detail lock + parser.

Background work can no longer hold a resource that foreground Radar admission requires.

## 2. Background maintenance pauses for the whole AutoScan round

`AdaptiveTrafficManager` now has an explicit low-priority background pause counter.

When an AutoScan round enters its runner:

- no new background Radar sweep view/detail request may start;
- no new 400+ Verified Organic Velocity checkpoint may start;
- already leased short requests may finish;
- foreground scan/view traffic remains available.

The pause is always released when the round finishes, is stopped, or the runner exits with an error.

The Bump Resurrection sweep also checks the traffic snapshot before every family/listing and yields when foreground work starts. The maintenance scheduler defers historical sweep/backfill entirely while user scans or AutoScan are active. An interrupted sweep is **not** allowed to mark the one-time sweep complete.

## 3. Real hard Stop

The admin button is now:

`⏹ Остановить сейчас`

Pressing it:

1. persists `status=paused` immediately in PostgreSQL, so a Railway restart cannot resurrect the round;
2. signals a process-local stop event;
3. cancels the currently owned AutoScan category task;
4. releases `scan_job_started()` accounting in `finally`;
5. resets the category browser context with a bounded cleanup;
6. keeps `current_index` unchanged.

Therefore Resume starts the interrupted category again from a clean state instead of advancing past an incomplete result.

Stop also interrupts:

- waiting for user scans;
- partial/system cooldown;
- success gap.

It no longer waits for the current category to finish.

Already dispatched short Page/Date worker probes may finish in their own Railway worker, but AutoScan no longer waits for them and does not start another category while paused.

## 4. Full-category watchdog

AutoScan now owns a hard watchdog around the **entire category pipeline**:

`Date/Page scan -> exact views -> Organic Detail Gate -> Radar admission`

Default:

`RADAR_AUTOSCAN_CATEGORY_TIMEOUT_SECONDS=480` (8 minutes)

If the watchdog fires:

- the category task is cancelled;
- the category is stored as `⚠️ допроверка`;
- the parser/browser session is recycled;
- the round advances to the next category instead of freezing all 84 categories.

This is separate from the normal user-scan watchdog and does not change user scan behavior.

## 5. Dedicated View Worker wait is bounded for AutoScan

The generic View Manager can historically wait much longer for a healthy-but-stuck remote batch. AutoScan now adds its own exact-recovery watchdog:

`RADAR_AUTOSCAN_VIEW_RECOVERY_TIMEOUT_SECONDS=240` (4 minutes)

If it expires:

- already obtained direct exact counters are preserved;
- unresolved counters remain unknown;
- the category fails closed as incomplete and goes to retry;
- AutoScan does not wait 30 minutes inside one category.

## 6. Live AutoScan stage in admin

`0/84` only means zero categories have fully completed. The admin card now also exposes the live phase of category 1:

- `🔎 поиск даты · запросов N · стр. X`
- `📄 страницы X/15 · объявлений N`
- `👁 точные просмотры X/N`
- `⚙️ Organic detail-check`

The card also shows the configured category watchdog. This removes the false impression that nothing is happening while the first category is actively working.

## 7. Verified Organic Velocity is unchanged

v4.15.7 behavior remains exactly intact:

- the 400 threshold applies only to the first exact counter DT observes;
- first observation `>=400` contributes zero inherited views to Score;
- two later clean exact checkpoints at least 30 minutes apart are required;
- after certification only `current_views - baseline_views` is used;
- high views alone never mark an ad promoted.

DT Demand Score remains:

- 40% Relative View Velocity
- 20% Acceleration
- 15% Persistence
- 15% Repeatability
- 10% Price Fit

## 8. Database / Railway

No manual SQL migration is required.

No new required Railway variable is required. Optional overrides:

```text
RADAR_AUTOSCAN_CATEGORY_TIMEOUT_SECONDS=480
RADAR_AUTOSCAN_VIEW_RECOVERY_TIMEOUT_SECONDS=240
```

Recommended deployment from the same v4.15.8 checkout:

1. **Parser — required.** This is where the deadlock, hard stop, AutoScan watchdog and admin live stage are fixed.
2. **AI Worker — recommended** for one-version consistency; scoring semantics are unchanged.
3. **Lifecycle Worker — recommended** for one-version consistency.
4. **Page Worker / Date Worker — recommended** from the same checkout; their parsing algorithms are unchanged.
5. **View Worker — recommended** from the same checkout; its algorithm is unchanged, while Parser now bounds how long AutoScan waits for it.

## 9. Expected startup log

After deploy Parser should include:

```text
[service-launcher] version=4.15.8 service='parser' ...
Starting @DTTEAM_PARSER_BOT | version=4.15.8 ...
v4.15.8 AutoScan Deadlock & Hard Stop online | category_watchdog=480s | view_recovery_watchdog=240s | background_pause=round | detail_lanes=foreground+background
```

## 10. Smoke checks

1. Start AutoScan: the panel should show category `Autos` plus a live stage instead of only static `0/84`.
2. While AutoScan is running, Bump Resurrection / Verified Organic Velocity should stop issuing new background detail/view requests from Parser.
3. Press `Остановить сейчас` during Date/Page: status becomes paused and category does not advance.
4. Resume: the same interrupted category starts again.
5. Press Stop during exact views: the View Manager task is cancelled; remote View Worker receives its existing cancellation key when applicable.
6. Press Stop during Organic Detail Gate: foreground detail task is cancelled and the foreground lock is released.
7. Simulate a category longer than 480s: it becomes `⚠️ допроверка`, parser is recycled, next category starts.
8. Simulate a remote exact recovery longer than 240s: unresolved views stay unknown and category goes to retry.
9. Background sweep interrupted by AutoScan must not set `dt_radar_v4156_bump_sweep_complete=1` merely because the partial batch had zero UNKNOWNs.
10. First-seen `399` / `400` / `942` / `16,337` Verified Organic Velocity semantics must remain identical to v4.15.7.

---

## Source: `DEPLOY_V4_8_1_GOLDEN_CORE.md`

# DT PARSER v4.8.1 — Golden Core

This build is an A/B control release.

## Parsing core
The following files are copied byte-for-byte from v4.4.0 Stability Hardening:

- `parser.py`
- `stable_engine.py`
- `traffic.py`
- `distributed.py`
- `date_manager.py`
- `date_worker.py`
- `page_manager.py`
- `page_worker.py`
- `view_manager.py`
- `view_counter_worker.py`

No performance changes from v4.8.0 remain in these files.

## What stays from the newer product branch
- RU/EN user interface and language selection
- current admin UI and worker/user scan views
- AI Lab / Product Opportunity Engine
- current commerce/database/UI features outside parsing core

## Railway test
Deploy all services from the same commit and wait for Date/Page/View workers to be online. Run one 50-page scan first, then repeat the identical scan without redeploying to compare cold vs warm workers.

This release intentionally uses the original v4.4.0 Redis worker namespaces. Do not mix old v4.4/v4.6 worker deployments with v4.8.1 at the same time.

---

## Source: `DEPLOY_V4_8_2_RESILIENT_TRAFFIC.md`

# DT PARSER v4.8.2 — Resilient Traffic

Base: v4.8.1 Golden Core. Parser/stable-engine logic is unchanged.

## Refusal behavior for Date/Page/View workers
- Redis shared cooldown is disabled for all three worker fleets.
- 403: no process-wide hard pause. The refused request fails normally; only a bounded local penalty remains.
- 429: short local hard pause, capped at 3 seconds.
- Maximum local penalty: 1 level.
- Recovery: 10 successes and 10 seconds quiet instead of the historical 60/60 profile.
- Distributed/global request limits remain enabled.
- View adaptive pool remains enabled, reduces by one slot on refusal, and can recover after a 3-second growth hold.

This release does not try to bypass refusals. It prevents one refusal from freezing unrelated replicas or putting a whole fleet into long exponential backoff.

## Railway
Remove any manual `DIST_TRAFFIC_SHARED_COOLDOWN` override from Date/Page/View; the worker entrypoints force the correct value. Redeploy all three worker services from the same commit.

---

## Source: `DEPLOY_V4_8_3_RELIABLE_CORE.md`

# DT PARSER v4.8.3 — Reliable Core

Основа: v4.8.2 / Golden 4.4 parsing lineage. Релиз меняет только P0-участки, которые создавали искусственные паузы или плохое распределение задач.

## P0 изменения

1. **Bot/local fallback = resilient traffic**
   - 403: hard-pause 0 сек;
   - 429: локальная пауза до ~3 сек;
   - penalty максимум 1;
   - восстановление после 10 успешных запросов / ~10 сек.

2. **Fast-fail одной страницы**
   - category HTTP и browser transport делают максимум один короткий retry после 403/429;
   - старые request-local recovery windows 45–180 сек больше не удерживают scan lane;
   - после повторного отказа вызывающий код получает `TemporaryAccessError` и может продолжить recovery/fallback без блокировки всей очереди.

3. **Release-scoped Redis runtime**
   - Date jobs/pending/error/heartbeat: `dtparser:dateworker:runtime:v483:*`;
   - Page jobs/pending/error/heartbeat: `dtparser:pageworker:runtime:v483:*`;
   - View jobs/progress/result/worker heartbeat: `dtparser:viewcounter:runtime:v483:*`;
   - Date cache/predictor и Page cache остаются на стабильных старых ключах и не теряются.
   - Старые runtime jobs v4.8.2/4.8.1 больше не могут быть reclaimed новым worker fleet. Ручная очистка Redis при этом релизе не нужна.

4. **Fresh jobs first**
   - Date/Page/View сначала читают новые stream jobs;
   - `XAUTOCLAIM` crash-recovery выполняется только когда свежая очередь пуста.

5. **Page identity без жёстких 25 слотов**
   - `/seite:N` в final URL является главным подтверждением номера страницы;
   - result stride выводится из фактического offset, когда это возможно;
   - изменение количества organic slots больше не должно создавать массовое `verified=True matches=False` на корректных страницах;
   - redirect на другую `/seite:M` всё ещё отклоняется.

6. **Быстрый Page handoff**
   - `PAGE_CACHE_WAIT_MS=450` вместо 1800;
   - polling 75 мс;
   - если remote Page Worker не успел, foreground быстрее продолжает fallback вместо ожидания почти 2 секунд на каждой странице.

7. **View sharding для обычного скана**
   - sharding начинается с 40 URL;
   - target shard ~18 URL;
   - ожидаемый fleet = 4 View Worker;
   - типичный batch 50–60 объявлений делится примерно на 4 независимых shard, поэтому четыре реплики реально работают на один пользовательский scan.

## Что намеренно не менялось

- Stable Engine и финальная проверка границы даты;
- фильтры и dedupe;
- extraction объявлений;
- алгоритм точного view counter;
- RU/EN, админка, AI Lab, Product Opportunity Engine.

## Деплой

Все сервисы, которые используют runtime queues, должны быть на одном релизе **4.8.3 одновременно**:

- parser/bot;
- Date Worker;
- Page Worker;
- View Worker.

Из-за нового `runtime:v483` старый v4.8.2 worker не увидит новые jobs от v4.8.3 bot и наоборот. На Railway после обновления репозитория дождись, пока все четыре сервиса покажут новый launcher log `version=4.8.3` и workers снова будут 4/4.

Ручные Variables `DIST_TRAFFIC_SHARED_COOLDOWN=0` не нужны: профиль встроен в код.

## Первый тест

1. Один scan, 50 страниц, та же категория/дата, которую использовали для A/B.
2. Отдельно записать время Date / Pages / Views.
3. В логах проверить:
   - после 403 нет пауз 15–30 сек;
   - Page Worker перестал массово писать `verified=True matches=False` на корректных `/seite:N`;
   - View Manager пишет sharding для 50–60 URL примерно на 4 shard;
   - четыре View Worker получают разные `View job admitted`.
4. После успешного одиночного теста запустить два scan одновременно.

## Rollback

Rollback на v4.8.2 безопасен: его старые Redis namespaces не были удалены. После rollback v4.8.2 снова будет использовать свои старые runtime keys, а `runtime:v483` со временем истечёт по TTL.

---

## Source: `DEPLOY_V4_8_4_SCAN_INTEGRITY.md`

# DT PARSER v4.8.4 — Scan Integrity

## Что исправлено

- Неполный crawl больше не переходит в `views`.
- `views` стартует только при `request_complete=True`.
- При partial сначала работает bounded auto-recovery.
- Если recovery завершил покрытие, просмотры собираются один раз на полном проходе.
- Если recovery не смог подтвердить все участки, scan остаётся partial; подтверждённые страницы сохраняются, но Views не маскирует недостающий page coverage.

## Railway

Обновить parser, Date Worker, Page Worker и View Worker на 4.8.4. Runtime namespace остаётся совместимым с 4.8.3 (`runtime:v483`), поэтому обязательной очистки Redis между 4.8.3 и 4.8.4 нет.

---

## Source: `DEPLOY_V4_8_5_INTEGRITY_RECOVERY.md`

# DT PARSER v4.8.5 — Integrity Recovery + Smart Date Hint + Clean Export

## Что изменено

- Quality telemetry теперь считает уникальные логические страницы, а не каждую сетевую попытку. Один page с четырьмя retry = один дефект.
- `repeated-content` больше не штрафуется одновременно как `repeated` и как generic `invalid`.
- Успешный повтор той же страницы снимает старый quality penalty и заменяет её метрики актуальными.
- Региональный crawl больше не становится `done` только потому, что набрал числовой лимит страниц, если хотя бы один обработанный участок остался `unresolved`.
- Такой результат остаётся `partial` и автоматически проходит уже существующий bounded recovery. Сильные страницы берутся из PostgreSQL checkpoints, поэтому сеть повторно тратится прежде всего на слабые/недостающие участки.
- Date Worker hint проверяется максимум в двух направленных шагах до local fallback: сначала hint, затем одна соседняя страница в сторону target по хронологии. Далёкий hint больше не тратит 5–6 foreground probes.
- В обычном XLSX/CSV оставлена одна колонка цены — `Цена, €`. Дублирующая текстовая `Цена` удалена.
- Колонка `👁 Просмотры` сохранена как отдельная числовая колонка сразу после цены.

## Railway

Обновить parser, Date Worker, Page Worker и View Worker на 4.8.5. Redis runtime namespace совместим с v4.8.3/v4.8.4 (`runtime:v483`), обязательная ручная очистка Redis для перехода 4.8.4 -> 4.8.5 не требуется.

---

## Source: `DEPLOY_V4_8_6_COVERAGE_COMPLETE.md`

# DT PARSER v4.8.6 — Coverage Complete

Основа: v4.8.5 Integrity Recovery.

## Что исправлено

- Региональный crawl считается полным, когда реально собрано нужное количество подтверждённых target-date страниц (15/25/50), даже если один слабый региональный page был отброшен и глубина была добрана другой подтверждённой страницей.
- `repeated-content` по-прежнему не попадает в результат как надёжная страница и остаётся штрафом качества, но больше не превращает корректные `50/50` в ложный partial.
- Partial остаётся только при реальном shortfall подтверждённой глубины.
- Лог `Deferred views skipped` теперь показывает реальное количество подтверждённых collection-pages, включая regional fill.
- Partial scan больше не показывает ложное `В результате: 0`: пользователю показывается число уже подтверждённых объявлений.
- Для partial snapshot временно не применяется `min_views`, потому что финальная Views-фаза законно ещё не выполнялась; остальные фильтры сохраняются.
- Финальный XLSX автоматически отправляется только для полного scan. Partial хранит подтверждённые данные и предлагает повтор/открытие сохранённого scan.
- Автоматические +3/+6/+12 view observations не планируются для partial scan.

## Важно

Date/Page/View алгоритмы, resilient traffic, Redis runtime `runtime:v483`, Smart Date Hint, чистый XLSX и View sharding из 4.8.5 сохранены.

Для перехода с 4.8.5 ручная очистка Redis не требуется.

---

## Source: `DEPLOY_V4_8_7_BROADCAST_LAUNCH.md`

# DT PARSER v4.8.7 — Broadcast Launch

Based on the known-good v4.8.6 Coverage Complete parser core.

## Added

- Admin panel button `📣 Рассылка`.
- One unified composer: send text, photo, or photo + caption to the bot.
- Exact Telegram preview before delivery.
- Explicit confirmation; nothing is broadcast immediately after upload.
- Uses `copy_message`, so formatting/captions/photo quality are preserved without a forwarded-message header.
- Sends to every registered non-banned `bot_users` record, including expired subscribers.
- Delivery report: sent / bot blocked or chat unavailable / other failures.
- Throttled delivery with Telegram RetryAfter handling.

## Parser core

No Date/Page/View/scan-integrity behavior changed from v4.8.6.

---

## Source: `DEPLOY_V4_8_8_READ_ONLY_HISTORY.md`

# DT PARSER v4.8.8 — Read-only History Access

Based on the known-good v4.8.7 Broadcast Launch / v4.8.6 parser core.

## Added

- Users with an expired subscription can open the main menu.
- `📊 Мои сканы` and the archive remain available after expiry.
- Saved scan cards, TOP-12/TOP-50, growth/history and XLSX export remain readable.
- Read-only home clearly shows that the subscription is inactive.
- `🔒 Новый скан` leads to the subscription screen.

## Still requires an active subscription

- New scans.
- Repeat/recheck scans.
- Manual view refresh (network work).
- Categories/settings/auto-observation changes and other active parser functions.

Banned users remain blocked. Admin-only access mode keeps its original semantics.

## Parser core

No parser, Date Worker, Page Worker, View Worker, Redis runtime, traffic, scan integrity, filters, database models or AI parsing behavior changed from v4.8.7/v4.8.6.

---

## Source: `DEPLOY_V4_8_9_QUEUE_UX.md`

# DT PARSER v4.8.9 — Queue UX

UI-only queue release on top of v4.8.8.

## Behaviour
- Up to `MULTIUSER_LOCAL_WORKERS` scans run simultaneously (default 4).
- Extra launches remain queued FIFO.
- User card shows live position, occupied lanes, people ahead, and wait time.
- Position is updated by the existing progress ticker.
- Queue cancellation does not start network work.
- When a lane opens, the same card immediately switches to scan progress.
- If queue wait was >= `QUEUE_START_NOTIFY_AFTER_SECONDS` (default 8s), a one-time start notification is sent.

No parser, Date Worker, Page Worker, View Worker, traffic or Redis runtime logic was changed.

---

## Source: `DEPLOY_V4_9_0_FREE_TRIAL_LAUNCH.md`

# DT PARSER v4.9.0 — Free Trial Launch

Product/access release on top of v4.8.9 Queue UX. The proven parsing core and Date/Page/View worker protocols are unchanged.

## Launch offer
- New never-paid users get 2 free scan credits while the campaign is enabled.
- Each trial scan: 1 category, 15 or 25 pages (maximum 25).
- Trial includes the real scan result, real views, TOP-12/TOP-50 and XLSX.
- Subscription remains required for 50 pages, multi-category scans, repeat/recheck/manual view refresh and +3/+6/+12h auto measurements.
- Trial scans are saved in `My scans` like normal scans.
- A queued trial cancelled before network work returns its credit. If a queued job is retired as stale before it starts, its credit is also refunded. Distributed queues otherwise survive normal parser-service restarts and continue normally.

## Admin
`🎁 Бесплатные сканы` shows campaign state and funnel stats:
- used at least one trial
- used all free credits
- converted to a paid subscription
- conversion percentage

The campaign can be enabled/disabled from the admin panel without redeploying.

## Database
Additive migration only:
- `bot_users.trial_scans_used`
- `user_scans.is_trial`
- `user_scans.trial_credit_refunded`

No Redis cleanup is required.

---

## Source: `DEPLOY_V4_9_1_FOUR_LANE_GUARANTEE.md`

# DT PARSER v4.9.1 — Four-Lane Queue Guarantee

## Why this release exists

v4.9.0 already intended to run four user scans at once, but the main parser mode was still selected from Railway environment variables. A stale mode value could therefore put the Telegram service onto the Redis/distributed parser path, where `MULTIUSER_LOCAL_WORKERS=4` no longer controlled user-facing parser capacity. The admin screen could then show a pattern such as `2 active / 2 queued` even though Date/Page/View helper replicas were healthy.

v4.9.1 makes the intended production contract code-owned instead of configuration-owned.

## Guaranteed behavior

For the main Railway service started with:

```bash
python bot.py
```

the code now guarantees:

```text
users 1–4  -> local scan-worker-1..4 -> running
user 5+    -> scan_queue FIFO        -> queued until a lane is free
```

Trial and paid scans share this exact queue. There is no two-user trial limit.

The main bot pins before importing `distributed.py`, `traffic.py`, or `parser.py`:

```env
STABLE_SINGLE_SERVICE_MODE=1
MULTIUSER_STABLE_MODE=1
MULTIUSER_LOCAL_WORKERS=4
```

`MAX_CONCURRENT_JOBS` is then hard-set to four in Stable Single Service mode. Stale Railway values cannot silently lower the number of user consumers.

## Truthful admin status

A local worker now changes the matching `user_scans.status` row from `queued` to `running` immediately when it claims the job, before parser/browser setup. `👀 КТО СЕЙЧАС ПАРСИТ` therefore reflects actual ownership of all four lanes. The headline also shows `running/4`.

## Fail-fast safety

At startup the main bot verifies that:

- Stable Single Service is active;
- distributed foreground scan execution is off in the Telegram service;
- Multi-User Stable is active;
- exactly four user lanes are configured;
- exactly four `scan-worker-*` tasks are created.

If a future code change breaks that contract, the parser process exits visibly instead of silently running at reduced capacity.

Expected startup lines include:

```text
Starting @... | version=4.9.1 | mode=local ... local_workers=4 ...
v4.3.2 Multi-User Stable active | parser_lanes=4 ...
Scan worker #1 started
Scan worker #2 started
Scan worker #3 started
Scan worker #4 started
v4.9.1 Four-Lane Queue Guarantee online | parser_lanes=4 | fifth_plus=FIFO | trial_and_paid_same_queue=True ...
```

## Railway deployment

Redeploy only the main `parser` service from the v4.9.1 code first. No PostgreSQL migration is required. No new required Railway variable is required.

Keep the existing helper services unchanged:

- Date Worker × existing replicas
- Page Worker × existing replicas
- View Worker × existing replicas
- AI Worker as already configured
- Redis and PostgreSQL unchanged

The helper entrypoints explicitly set `STABLE_SINGLE_SERVICE_MODE=0` inside their own process, so the bot-level four-lane pin does not convert them into local user parsers.

## Smoke test

1. Redeploy `parser`.
2. Confirm the five startup lines above.
3. Start four scans from four accounts within a short window.
4. Admin `КТО СЕЙЧАС ПАРСИТ` should reach `4/4` active and `0` queued.
5. Start a fifth account. It should show `4/4` active and `1` queued.
6. Let one active scan finish; the fifth should automatically become `running`.
7. Repeat with a mix of free-trial and paid accounts; capacity must remain the same.

## Scope

This is a queue/orchestration hardening release. Date discovery, page collection, accurate views, regional coverage, filtering, TOP calculations, exports, and the v4.9.0 free-trial product rules are otherwise unchanged.
