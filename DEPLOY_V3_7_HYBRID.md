# DT PARSER v3.7.1 — Browser → HTTP Hybrid на Railway

## Зачем этот режим

`browser-worker ×5` из v3.6.0 даёт независимые Chromium-сессии, но Chromium остаётся тяжёлым по RAM/CPU и каждая category navigation рендерит браузерную страницу. В v3.7.1 браузер используется коротко: первый нормальный navigation создаёт сессию, затем storage state передаётся в Playwright APIRequestContext и основная работа идёт как HTTP без рендера.

Это не режим обхода ограничений. Если сайт явно отвечает 403/429 или возвращает challenge, hybrid worker не меняет транспорт, чтобы проигнорировать отказ — он применяет cooldown/ошибку как и обычный production parser.

## Production-сервисы

Оставь:

```text
bot             ×1
PostgreSQL      ×1
Redis           ×1
views-worker    ×1
hybrid-worker   ×5 replicas
```

Старые `browser-worker` и `parser-worker` на время теста лучше остановить, чтобы они не забирали задания из той же Redis Stream.

## 1. Код

Залей содержимое v3.7.1 в тот же GitHub repository и дождись deploy основного `bot`.

## 2. Hybrid worker

Создай Railway service из того же GitHub repository.

Start Command:

```bash
python hybrid_worker.py
```

Подключи те же references/variables, что и к bot:

```env
BOT_TOKEN=...
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
DISTRIBUTED_WORKERS=1
```

`hybrid_worker.py` сам задаёт безопасные defaults до импорта приложения:

```env
SCAN_TRANSPORT=hybrid
PARSER_WORKER_CONCURRENCY=1
SHARE_ACTIVE_CATEGORY_SCANS=0
DIST_TRAFFIC_SHARED_COOLDOWN=0
TRAFFIC_SCAN_CONCURRENCY=1
TRAFFIC_BROWSER_CONCURRENCY=1
```

## 3. Replicas

Для первого нагрузочного теста поставь **5 replicas** у `hybrid-worker`.

Цель теста: 5 разных Telegram-аккаунтов одновременно запускают по одному scan. Каждый Redis job должен быть забран отдельной replica, но после первого browser seed большая часть времени должна идти через лёгкий HTTP transport.

## 4. Рекомендуемые глобальные лимиты

На всех сервисах, которые участвуют в distributed parsing, используй одинаковые значения:

```env
HYBRID_SCAN_LANES=5
HYBRID_GLOBAL_LANES=8
DIST_TRAFFIC_VIEW_LIMIT=3
DIST_TRAFFIC_SHARED_COOLDOWN=0
```

Не увеличивай `DIST_TRAFFIC_SCAN_LIMIT` выше количества реально протестированных workers только ради скорости. Если внешний сайт начинает отвечать отказами, больше concurrency обычно ухудшает стабильность.

## 5. Hybrid tuning

Defaults уже встроены:

```env
HYBRID_HTTP_TIMEOUT_MS=18000
HYBRID_SESSION_TTL_SECONDS=900
HYBRID_HTTP_RETRIES=2
HYBRID_BROWSER_FALLBACK_LIMIT=3
HYBRID_CLOSE_BROWSER_AFTER_SEED=1
```

Для первого теста их не меняй.

`HYBRID_CLOSE_BROWSER_AFTER_SEED=1` — ключевой ресурсный параметр: Chromium закрывается после переноса session state и не висит в RAM во время всего прохода по category pages.

## 6. Что смотреть в Telegram

Во время поиска даты должна появиться строка:

```text
⚡ Browser → HTTP hybrid
```

Это подтверждает, что job выполняет hybrid worker.

## 7. Что смотреть в Railway Logs

Нормальная последовательность:

```text
DT PARSER worker online ... transport=hybrid
Hybrid session seeded | chromium_released=True | bulk_transport=api-http
```

Если HTTP-document действительно требует browser compatibility fallback:

```text
Hybrid HTTP compatibility fallback to Chromium ...
```

Если таких fallback много на каждом job, hybrid не даёт ожидаемой выгоды и надо смотреть конкретный HTML/статусы, а не увеличивать replicas.

Если видишь много `403/429`, основное ограничение уже находится не в CPU/RAM Chromium, а во внешнем сетевом доступе. В таком случае сначала снижай глобальную интенсивность и используй cache/incremental scan, а не добавляй ещё browsers.

## 8. Нагрузочный тест

Сначала одинаковый сценарий:

1. 5 аккаунтов.
2. По 1 категории на аккаунт.
3. Одинаковая дата.
4. 25 страниц.
5. Старт в течение 5–10 секунд.

Проверяем:

- все пять сразу получили progress;
- `Проверено запросов` растёт у всех;
- ни один scan не стоит несколько минут на одном heartbeat;
- Railway RAM у hybrid replicas заметно ниже browser-only варианта после seed;
- 403/429 не растут лавинообразно.

Только после стабильного теста 5×25 переходи к 5×50 и затем 5×100.


## v3.7.1: что изменилось при одновременном старте

Раньше Redis держал один общий `traffic:next:scan` timestamp для всех replicas. Это ограничивало всплески, но не гарантировало справедливость: быстрый worker мог снова первым получить следующий момент старта. Теперь у каждой Railway replica свой pacing key, а глобальные active-request counters по-прежнему общие. Поэтому 5 workers получают 5 независимых foreground lanes, но суммарный потолок остаётся контролируемым.

Если в Railway остались старые `DIST_TRAFFIC_SCAN_LIMIT=2/3`, hybrid worker больше не наследует их как foreground cap. Для hybrid регулируй `HYBRID_SCAN_LANES` и `HYBRID_GLOBAL_LANES`.
