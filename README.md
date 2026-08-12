# Kleinanzeigen Parser Bot v3.1.4

## v3.1.3 — 403 Recovery / Safe Multi-User

- A 403/429 during date location no longer immediately ends the category with zero successful pages.
- Interactive category-page requests may wait up to 180 seconds (configurable) behind the shared circuit breaker and retry after quiet cooldowns.
- Every refusal reduces process-wide concurrency; background view checkpoints also wait behind the same gate.
- Safer default network limits: 3 category requests, 4 view requests, 1 browser fallback, 7 total. `MAX_CONCURRENT_JOBS` may remain 4; the network gate decides how many requests actually run at once.
- If the public site is still refusing requests after the full recovery window, the current category is partial and the remaining categories in that same job are not hammered one-by-one. Existing successful category results remain saved.
- This is graceful backoff only; the release does not attempt to bypass Kleinanzeigen protections.


## v3.1.2 — Adaptive Traffic Manager

This release keeps v3.1.1 multi-category/date reliability and adds a process-wide
traffic controller for commercial multi-user operation.

- **4 user scan workers by default.** Different users can genuinely scan in parallel.
- **Separate request pools:** category pages, direct view counters, and Chromium fallback.
- **Interactive capacity reservation.** Automatic +1/+3/+6/+12/+24h checkpoints keep working,
  but while scans are active their direct-view concurrency is reduced so they cannot starve a new scan.
- **Global burst smoothing.** Page/view/browser requests from all parser instances are spaced,
  instead of every worker releasing a burst at the same instant.
- **Adaptive 403/429 circuit breaker.** A refusal lowers effective concurrency and starts one shared
  bounded cooldown for the whole process. After a quiet period and enough successful requests,
  capacity grows back automatically. The code does not attempt to bypass site protection.
- **Shared scans and cache remain enabled.** Identical category + date + depth requests reuse one
  in-flight scan/result rather than multiplying network traffic.
- **Actual measurement time remains authoritative.** Background popularity checkpoints can be delayed
  by traffic pressure without pretending they ran at an exact clock second.

Recommended starting values are included in `.env.example`; v3.1.4 uses lightweight direct-only mass view refreshes and lower view concurrency. Tune upward only after observing real 403 rate and latency.

## v3.1.1 — Multi-category isolation fix

- Every selected category performs its own independent date-location cycle.
- Large categories no longer accept a zero result from a truncated >50-page feed.
- If an official state feed is still too large, the parser automatically drills into smaller official location feeds discovered from that category page.
- Hidden location feeds are internal only; the user still sees category + date + 25/50/100 pages.
- Progress now clearly shows the current category number (for example 2/3).
- A category that cannot be fully verified is marked partial instead of silently becoming zero.


## Parser Quality & Stability

v3.1.1 is a reliability release built on v3.0.7. Popularity Tracker, automatic
1/3/6/12/24-hour view checkpoints, product recognition, My Scans and the
25/50/100 depth workflow are preserved.

### What changed

- **No false zero from weak dates.** A result page may prove that the selected
  calendar day is newer/older/absent only when enough listing cards have a
  trustworthy publication date. If date coverage is weak, the scan is marked
  partial instead of returning a confident zero.
- **Page identity verification.** The parser checks result offsets and the final
  pagination URL. Redirected/normalized pages are not used as chronology data.
- **Repeated-page protection.** Compact listing-ID fingerprints detect when two
  requested pages contain the same result set. Repeated content is rejected as a
  date signal.
- **Narrow date extraction.** Publication dates continue to be read from listing
  metadata rather than arbitrary card/title text.
- **Conservative promoted-ad filter.** Only explicit card-level promotion markers
  are removed; generic words in titles/layout are not used as a filter.
- **Quality telemetry.** Every real category response records raw cards, parsed
  listings, missing dates/prices, promoted cards, duplicates, invalid/repeated
  pages, view failures and a 0–100 quality score.
- **Persistent quality score.** New saved scans show their parser-quality score
  in `📊 Мои сканы`. Old scans display that no v3.1.1 quality measurement exists.
- **Better partial results.** A category exception, an unverified date boundary,
  or inaccessible feed now marks the saved scan partial instead of silently
  completing it.
- **Admin stats.** `📊 База и парсинг` now includes average v3.1.1 quality, missing
  dates, invalid pages and repeated pages for the current day.

### Exact-date behavior

The user still chooses:

1. category;
2. Moscow calendar date;
3. depth: **25 / 50 / 100**.

For recent dates the parser uses the verified public feed directly. If the date
is beyond Kleinanzeigen's public pagination window, the existing hidden shard
mechanism is used internally. These internal feeds are never shown to end users;
unique target-date listing IDs are merged into one saved scan.

A zero-result scan is considered complete only after the date boundary was
verified with reliable publication-date data. Otherwise the result is explicitly
`partial`.

### Automatic popularity measurements

Unchanged from v3.0.7: completed scans get public-view checkpoints at
`+1 / +3 / +6 / +12 / +24` hours. Category-separated growth TOPs show TOP-10 in
Telegram and can export TOP-50 XLSX.

### Parser-quality self tests

The archive contains `tests/test_parser_quality.py`. It covers:

- a date inside a product title not overriding the publication timestamp;
- promoted-card filtering;
- normalized page rejection;
- low date coverage becoming `unknown`;
- one-card target boundary handling;
- reliable newer/older direction classification.

Run locally with:

```text
python -m unittest discover -s tests -v
```

### Deployment

Railway start command remains:

```text
python bot.py
```

No new required variables. Existing SQLite/PostgreSQL databases are upgraded
with additive columns on startup. Optional v3.1.1 tuning:

```text
MIN_PAGE_DATE_COVERAGE=0.55
MIN_PAGE_DATED_ITEMS=3
```

For production/multi-user paid use, PostgreSQL is still recommended; SQLite is
appropriate for current development/testing but may not survive Railway
redeploys depending on storage configuration.


## v3.1.4 — Lightweight Views Engine + Responsive UI

- Массовые первичные и повторные замеры просмотров используют только быстрый direct HTTP counter. Chromium/browser fallback больше не запускается для сотен объявлений автоматически.
- Неудачные direct-счётчики помечаются как «без данных» и не тормозят весь скан. Browser fallback оставлен только для точечной диагностики одного объявления.
- Повторные замеры идут пакетами (по умолчанию 40 ID с короткой паузой), а автоматический observation worker по умолчанию один.
- `👁 Обновить просмотры` запускает фоновую задачу и мгновенно возвращает управление Telegram. Можно сразу открывать другие меню и запускать сканы.
- Повторное нажатие для того же скана не создаёт второй сбор. После окончания бот сам присылает уведомление и кнопки к динамике/скану.
- Фоновые ручные и автоматические замеры проходят через один лёгкий collector. После первого задания следующее заново проверяет DB cache, поэтому одинаковые ID из пересекающихся пользовательских сканов не запрашиваются повторно в течение 5 минут.
- Общий DB cache `views_checked_at` продолжает переиспользовать свежие значения между пользователями и пересекающимися сканами.
