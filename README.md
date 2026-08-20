# DT Parser v4.4.0 — STABILITY HARDENING

Большое обновление стабильности поверх v4.3.38. Product limits остаются: **до 2 категорий**, **5 дат** (сегодня + 4 предыдущих), **15/25/50 страниц**, Worker fleet **Date ×4 / Page ×4 / View ×4**.

## Что изменено

- **Date/Page Fleet Guard:** четыре реплики теперь делят Redis-лимиты по ролям и общий search-fleet budget; добавление реплик больше не умножает сетевое давление.
- **Rolling Page Prefetch:** Page Worker прогревает небольшое окно впереди и подкидывает новые страницы только пока выбранная дата реально продолжается.
- **DB write safety:** `upsert_page_items()` сам владеет `db_write_lock`, поэтому будущий/параллельный вызов не сможет забыть сериализацию `SELECT → INSERT`.
- **Partial View Recovery:** если один view-shard падает, результаты остальных сохраняются; локально пересчитываются только отсутствующие URL, а не весь batch.
- **Единая версия:** Date/Page/View heartbeat и service launcher читают общий `VERSION=4.4.0`; старые номера worker-версий больше не вводят в заблуждение.
- **Чистый production profile:** safety-critical старые Railway tuning variables игнорируются по умолчанию.
- **Regression tests:** добавлены тесты на rolling prefetch, 2-category limit, traffic buckets, view partial-shard recovery и ключевые release invariants.

## Что специально НЕ менялось

- Текущая московская логика определения/выбора даты оставлена как есть.
- `SCAN_CATEGORY_HARD_TIMEOUT_SECONDS=1200` (20 минут) оставлен как есть.
- Финальная точная проверка даты основным parser, строгий Page Worker quality gate и exact views semantics не ослаблялись.

## Railway

Оставить **Date Worker ×4, Page Worker ×4, View Worker ×4**. Новых обязательных Variables нет. После деплоя `v4.4.0` worker-сервисы сами применяют безопасный fleet profile.

---

# DT Parser v4.3.38 — STABILITY LIMITS

**Новые лимиты:** максимум **2 категории** на скан и только **5 дат** (сегодня + 4 предыдущих дня). Ручной ввод даты убран. Worker fleet: Date ×4 / Page ×4 / View ×4.

# DT Parser v4.3.36 — Four-User Fleet

Production profile: Date Worker ×4, Page Worker ×4, View Worker ×4. View replicas share a fleet-wide Redis budget of 16 official-counter HTTP requests and take one shard at a time for fair distribution/recovery. Regional old-date locator can keep four independent regions in flight. No new Railway variables are required.

## v4.3.35 VIEW FLEET GUARD
Shared Redis view limiter/cooldown for View Worker ×3; smaller pools/shards; exact parser unchanged.

# DT Parser v4.3.32 — SMART HYBRID OLD DATE

- Fixes v4.3.31 where a date deeper than the nationwide public window could finish in seconds with a false zero.
- Normal reachable dates stay nationwide-only.
- Verified `too_deep` dates automatically fall back to regional shards for correctness.
- `15/25/50` remains a maximum nationwide depth when the date is reachable nationwide.
- No Railway variable changes required.

## v4.3.31 — NATIONWIDE MAX DEPTH

This test release removes the regional hidden-fill from the default scan path.

- `15 / 25 / 50` now means **maximum nationwide target-date pages**, not a mandatory depth that must be filled from regional feeds.
- After the target date is found in the Germany-wide feed, the bot scans forward until one of three things happens: the selected date ends, the chosen max depth is reached, or Kleinanzeigen's public nationwide page window ends.
- If the selected date itself is deeper than the public nationwide window, the bot reports that limitation explicitly instead of starting a multi-minute regional crawl.
- Regional hidden-fill code is preserved as an opt-in rollback path: `REGIONAL_HIDDEN_FILL_ENABLED=1`. Default is OFF.
- Date Worker / Predictor / Cold Date Turbo / Page Worker / View Worker remain unchanged.


## v4.3.30 — REGIONAL SHARD FIX

Fixes the Cold Date Turbo regional recursion regression from v4.3.28/v4.3.29.
When a regional feed is proven deeper than page 50, the main stable parser now verifies page 1 first so Kleinanzeigen child-location shards are discovered before returning `too_deep`. The remote Date Worker remains a hint only and all page/date truth is still locally verified. Page Worker and View Worker logic are unchanged.
# DT PARSER v4.3.29 — COLD DATE TURBO + PARALLEL HIDDEN FILL

v4.3.29 targets the slowest remaining date scenario: the **first cold scan of an old date** (especially day 5–6 of the allowed 7-day window) and the second regional date-search phase that can appear after nationwide pages are exhausted.

## What changed

- **Cold Date Turbo**: first-ever date searches use one age-aware broad probe grid instead of the old dependent `1/2/4/8/16/32/50` ladder.
- With the recommended **Date Worker ×2**, each replica now defaults to **4 HTTP-first consumers**: up to 8 cheap date probes can be processed in parallel.
- Browser confirmation remains separately throttled to **1 per replica** by default, so HTTP parallelism does not turn into a Chromium storm.
- If a locally revalidated public page 50 is still newer than the target date, the bot jumps directly to the regional sharder instead of repeating the full local date ladder.
- **Parallel regional prewarm**: when a 50-page historical scan is likely to need regional depth, Date Worker starts warming several regional date boundaries while nationwide Page Worker collection is still running.
- Hidden/regional feeds keep a rolling prewarm window, so later regions are discovered in parallel instead of one long sequential date-search chain.
- Telegram now labels that phase as **Региональный добор даты** instead of making it look like the original date search restarted.
- Predictor/Continue Search from v4.3.26–v4.3.27 stays enabled for repeat scans.

## Accuracy / safety

- Remote Date Worker results are still **hints only**.
- The foreground stable parser still locally verifies the final date boundary.
- The new `beyond page 50` shortcut is accepted only after local verification that page 50 is still newer than the selected date.
- Weak HTTP probes still require browser confirmation.
- 403/429 handling remains unchanged.
- `parser.py`, `traffic.py`, Page Worker and View Worker core are unchanged.

## Railway

No new mandatory Railway variables or services are required. Keep the existing **Date Worker** service at **2 replicas**.

Optional rollback/tuning variables:

```env
DATE_COLD_TURBO_ENABLED=1
DATE_WORKER_CONCURRENCY=4
DATE_WORKER_BROWSER_CONFIRM_CONCURRENCY=1
HIDDEN_DATE_PREWARM_ENABLED=1
HIDDEN_DATE_PREWARM_WINDOW=6
HIDDEN_DATE_PREWARM_CONCURRENCY=4
```

The defaults above are already built in; you do not need to add them unless you want to tune or disable a feature.

See `DEPLOY_V4_3_28_COLD_DATE_TURBO.md`.


## v4.3.34 — Triple Worker Fleet

Recommended Railway replicas: Date Worker ×3, Page Worker ×3, View Worker ×3.
The bot defaults to 4 simultaneous local scan slots; regional Date pipeline can keep 3 locators in flight with a 6-region look-ahead. Per-worker concurrency remains unchanged for safety.

## v4.3.33 — Parallel Regional Locator + Regional/Page Pipeline

For old dates that are deeper than the nationwide 50-page window, regional fallback now pipelines work instead of waiting for every region serially:

- Date Worker pre-locates upcoming regional feeds in a rolling window while the current region is being verified/collected.
- Ready regional hints are consumed first, reducing foreground idle time.
- A precomputed remote hint is reused instead of launching the same Date Worker search twice.
- Every remote hint remains acceleration-only: the foreground stable parser still verifies the candidate boundary before any page is accepted.
- Page Worker/cache continues to prefetch the verified region while Date Worker works on the next regions.
- Regional pipeline timing is logged (`locator_wait`, `collect`) for real bottleneck measurement.

No new Railway variables are required. Defaults are conservative: 4 queued regional hints, at most 2 regional locator jobs running concurrently. Optional rollback: `REGIONAL_DATE_PIPELINE_ENABLED=0`.


## v4.3.37 DATE BOUNDARY RACE FIX
Prevents a deep Date Worker target hint from causing a 40+ page linear walk-back when several Date Worker replicas race. Wide brackets are remotely refined and foreground linear walk-back is bounded before falling back to the exact local locator.
