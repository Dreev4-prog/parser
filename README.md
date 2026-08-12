# Kleinanzeigen Parser Bot v3.2.2

## v3.2.2 — PostgreSQL Production

- Railway production now **requires PostgreSQL**. The bot no longer silently falls back to an ephemeral SQLite file when `DATABASE_URL` is missing.
- Railway `postgres://` / `postgresql://` URLs are normalized automatically for SQLAlchemy + asyncpg.
- Startup waits/retries while a newly-created Railway PostgreSQL service becomes reachable.
- PostgreSQL connection pooling is enabled with conservative defaults (`5 + 5` connections) and `pool_pre_ping`.
- All existing parser, scan history, view observations, users, subscription plans, invoices/payments and admin settings use the same PostgreSQL database through SQLAlchemy.
- Additive schema migrations remain automatic on startup.
- Local SQLite remains available only as a development/test fallback outside Railway.

### Railway PostgreSQL setup

1. In the Railway project choose **New → Database → PostgreSQL**.
2. Open the **parser** service → **Variables**.
3. Add `DATABASE_URL` with value `${{Postgres.DATABASE_URL}}` (replace `Postgres` only if your database service has a different name).
4. Redeploy the parser service.
5. Open `/admin` → database/parser statistics and verify the backend is `PostgreSQL`.

No PostgreSQL username/password/host variables need to be copied separately when `DATABASE_URL` references the Railway Postgres service.


## v3.2.1 — Telegram Menu

- Added the standard Telegram **Menu** button with ready-to-tap commands; users no longer need to type commands manually.
- User commands: `/start`, `/new_scan`, `/my_scans`, `/popular`, `/categories`, `/settings`, `/subscription`, `/result`, `/help`.
- Admin chats additionally see `/admin`.
- Command handlers open the same existing screens and workflows as the inline buttons, so parser/payment logic remains unchanged.
- `/subscription` stays reachable for users without active access.

## v3.1.7 — Clean Live Progress

- Simplified the live scan message to the essentials: current category, selected-date search state, page progress, listing count, view count, elapsed time, and one progress bar.
- Hidden start-page numbers, date-coverage telemetry, parser quality diagnostics, internal phase labels, and other implementation details from the normal live UI. Diagnostics remain available in logs and final quality/warning handling.
- Removed the extra “Launching scan” message. A scan now creates one live status card that changes from preparation → date search → collection.
- Multi-category progress remains visible as `current/total`, while each category still performs its own independent date search.


## v3.1.6 — Clean Scan UI

- Removed the user-facing **Models** section/button. Recognition data may remain internal for future analytics, but it is not shown in everyday scan UX.
- Compact scan card: date/depth/quality, result, observation. Successful diagnostic noise is hidden.
- Simplified actions: **Update views**, **Top views**, **Top growth**, **Repeat scan**, **History**, **Download result**.
- Model labels were removed from Telegram Top/Top growth and from the TOP-50 growth workbook.
- Legacy Model buttons from old messages safely return to the scan card.


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

Recommended starting values are included in `.env.example`; v3.1.6 uses real direct-only control measurements and lower view concurrency. Tune upward only after observing real 403 rate and latency.

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

`DATABASE_URL` is required on Railway in v3.2.2. PostgreSQL databases are upgraded
with additive columns on startup. Optional v3.1.1 tuning:

```text
MIN_PAGE_DATE_COVERAGE=0.55
MIN_PAGE_DATED_ITEMS=3
```

On Railway v3.2.2 uses PostgreSQL only. SQLite is retained solely for local
development/testing outside Railway.


## v3.1.4 — Lightweight Views Engine + Responsive UI

- Массовые первичные и повторные замеры просмотров используют только быстрый direct HTTP counter. Chromium/browser fallback больше не запускается для сотен объявлений автоматически.
- Неудачные direct-счётчики помечаются как «без данных» и не тормозят весь скан. Browser fallback оставлен только для точечной диагностики одного объявления.
- Повторные замеры идут пакетами (по умолчанию 40 ID с короткой паузой), а автоматический observation worker по умолчанию один.
- `👁 Обновить просмотры` запускает фоновую задачу и мгновенно возвращает управление Telegram. Можно сразу открывать другие меню и запускать сканы.
- Повторное нажатие для того же скана не создаёт второй сбор. После окончания бот сам присылает уведомление и кнопки к динамике/скану.
- Фоновые ручные и автоматические замеры проходят через один лёгкий collector. После первого задания следующее заново проверяет DB cache, поэтому одинаковые ID из пересекающихся пользовательских сканов не запрашиваются повторно в течение 5 минут.
- Общий DB cache `views_checked_at` продолжает переиспользовать свежие значения между пользователями и пересекающимися сканами.


## v3.1.6 — Real View Snapshots + Animated Background Progress

- Manual `👁 Обновить просмотры` runs as a true background task and immediately shows a separate live percentage/progress message.
- The Telegram UI remains usable while the measurement runs; progress edits are throttled to roughly once every 1.5 seconds / batch.
- Manual and scheduled 1/3/6/12/24h checkpoints require genuinely fresh Direct View values. The normal multi-minute cache can no longer create a fake observation point.
- A tiny `VIEW_MEASUREMENT_REUSE_SECONDS` window (20s default) only coalesces truly simultaneous measurements across users.
- If zero fresh values are available, no new observation point is created.
- Completion replaces the progress message with a clear summary: fresh values, direct requests, simultaneous reuse, missing counters, listings that grew, maximum and total growth.
- Chromium/browser fallback remains disabled for mass measurement jobs.

## v3.1.8 — Private sellers only

- Commercial/store listings are excluded through Kleinanzeigen's official `Anbieter: Privat` search filter.
- Store ads no longer consume the selected 25/50/100-page depth.
- They are not saved into scan snapshots and therefore do not enter view refreshes, popularity or growth TOPs.
- No extra detail-page requests are needed; the filter is applied to the category/search URL itself.
- Set `FILTER_BUSINESS_SELLERS=0` only if commercial sellers should be included again.

## v3.2.0 — Admin & Subscriptions

The parser core from v3.1.8 is unchanged. v3.2.0 adds a commercial access layer around it.

### Admin panel

Open `/admin` from a Telegram account whose ID is listed in `ADMIN_IDS`.

The panel shows user/activity/scan/payment statistics and lets an admin:
- find users by Telegram ID, username or name;
- grant 1/3/7/30 days manually, revoke access, ban/unban;
- edit subscription prices and enable/disable plans;
- view recent invoices/payments;
- switch access mode between `admin_only`, `subscription`, and `open`.

### Subscription payments

Two invoice providers are supported:
- CryptoBot / Crypto Pay: set `CRYPTO_PAY_TOKEN`;
- xRocket Pay: set `XROCKET_API_KEY`.

Invoices are created in USDT. The bot polls pending invoices every `PAYMENT_POLL_SECONDS` seconds, so a separate webhook web server is not required for this first commercial build. When a provider confirms `paid`, access is extended from the later of now or the user's current subscription end, and the user receives a Telegram notification.

The default plans are 1, 3, 7 and 30 days. Environment prices are only used when plans are created for the first time; afterwards change prices from `/admin` and they persist in the database.

### Safe rollout

Keep `ACCESS_MODE=admin_only` while testing. After both payment tokens are configured and a real test invoice has been checked, switch to `subscription` from `/admin`.

v3.2.2 requires Railway PostgreSQL in production so user access, payments, scans and background history survive redeploys reliably.
