# Kleinanzeigen Parser Bot v2.8.3


This version tests and enables a faster public view-counter path.

## What changed

- The parser first tries the public counter endpoint used by the Kleinanzeigen ad page (`s-vac-inc-get.json?adId=...`) directly.
- If plain HTTP is accepted, no ad page is rendered for that counter.
- If plain HTTP is not accepted, the parser tries Playwright's shared `APIRequestContext` (same browser session/cookies, but still without rendering a page).
- Only counters that cannot be read directly fall back to the lightweight Chromium page method from v2.8.0.
- The working direct mode is probed once per parser instance and then reused for the rest of the scan.
- Direct counter requests run concurrently; browser fallback remains more conservative.
- Existing 30-minute view cache remains active, so recently checked listings are not requested again.
- The Telegram button is now `⚡ Тест быстрых просмотров`: it compares direct-counter time against Chromium time on one ad.

## Important

Opening an ad or calling the counter endpoint may increment the public counter. The existing cache limits repeated checks. No CAPTCHA/access-control bypass is implemented.

## Optional Railway variables

```env
# Fast direct counter requests
DIRECT_VIEW_CONCURRENCY=8

# Browser fallback limits
VIEW_COUNT_CONCURRENCY=5
VIEW_COUNT_GLOBAL_CONCURRENCY=6

# Reuse a recent counter instead of checking again
VIEW_COUNT_CACHE_TTL_SECONDS=1800

# Category scan stability
PAGE_DELAY_SECONDS=1.0
CATEGORY_HTTP_RETRIES=3
CATEGORY_403_BACKOFF_SECONDS=10
CATEGORY_RETRY_JITTER_SECONDS=2
```

Start command remains:

```bash
python bot.py
```


## v2.8.0 — User Scan Hub

- Новый простой главный экран: Популярное / Новый скан / Мои сканы / Категории / Настройки.
- Каждый запуск сохраняется как карточка в `Мои сканы`.
- Снимок объявлений и исходных просмотров фиксируется в момент завершения.
- Из карточки можно обновить просмотры, увидеть лидеров и рост, скачать файл именно этого скана или повторить его.
- Рост считается относительно просмотров на момент завершения скана.
- Текущий быстрый direct-view механизм v2.7.2 сохранён без изменений.

> Важно: при SQLite история живёт в локальном файле контейнера. Для гарантированного хранения между redeploy/restart перед публичным запуском рекомендуется PostgreSQL.

## v2.8.2 — фильтр рекламных/продвигаемых объявлений

По умолчанию карточки с явными признаками платного продвижения Kleinanzeigen исключаются до сбора просмотров и записи в БД. В v2.8.2 фильтр стал консервативным: он проверяет точные классы wrapper-а `badge-topad`, `is-topad`, `is-highlight` и явные рекламные badge-метки, не сканируя обычные классы/родителей по словам `highlight` или `sponsor`.

Важно: фильтр не использует правило «много просмотров за короткое время», поэтому настоящее свежее вирусное объявление не отбрасывается только из-за высокого счётчика.

Переменная Railway (обычно менять не нужно):
`FILTER_PROMOTED_LISTINGS=1` — фильтр включён (по умолчанию). Поставить `0`, чтобы временно вернуть продвигаемые карточки.


## v2.8.3 — поиск по точной дате (МСК)
- «Новый скан» сначала спрашивает конкретную дату.
- Можно отправить день текущего месяца (`12`) или полную дату (`10.08.2026`).
- Все пользовательские даты/время показываются по Europe/Moscow (UTC+3).
- `Heute/Gestern + HH:MM` Kleinanzeigen переводятся из немецкого времени в московскую календарную дату.
- Скан идёт по выдаче до выбранного дня и останавливается после того, как прошёл его.
- Карточка «Мои сканы» хранит выбранную дату; повтор скана использует ту же дату.
- CSV содержит отдельную колонку `Дата (МСК)` и исходное отображение даты Kleinanzeigen.
