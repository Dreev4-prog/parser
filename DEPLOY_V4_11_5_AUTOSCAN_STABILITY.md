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
