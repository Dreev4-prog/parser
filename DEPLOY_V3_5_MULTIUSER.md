# DT PARSER v3.5.0 — Railway Multi-User deployment

## Цель

Сделать так, чтобы Telegram-интерфейс не зависел от тяжёлого crawl, а 4–5 пользователей могли реально иметь отдельные активные parser workers одновременно. PostgreSQL хранит данные; Redis координирует очередь, shared scans, progress, cancel и общий сетевой limiter.

## Сервисы в одном Railway project

Нужны:

1. `bot` — тот же GitHub repo, Start Command: `python bot.py`, **1 replica**.
2. `parser-worker` — тот же repo, Start Command: `python parser_worker.py`, начни с **5 replicas** и `PARSER_WORKER_CONCURRENCY=1`.
3. `views-worker` — тот же repo, Start Command: `python views_worker.py`, **1 replica**.
4. `Postgres` — существующая база DT PARSER.
5. `Redis` — новая Railway Redis database/service.

Если в интерфейсе Railway для твоего тарифа неудобно выставлять 5 replicas одного worker service, можно временно создать несколько одинаковых parser-worker services из одного repo. Redis Streams безопасно распределит jobs между ними.

## Общие Variables для bot + parser-worker + views-worker

```env
BOT_TOKEN=...
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
DISTRIBUTED_WORKERS=1
FILTER_BUSINESS_SELLERS=1
FILTER_PROMOTED_LISTINGS=1
```

В `bot` оставь также `ADMIN_IDS`, ACCESS_MODE, CryptoBot/xRocket и тарифы.

## Parser worker Variables

Рекомендуемый стартовый профиль для 5 simultaneous scans:

```env
PARSER_WORKER_CONCURRENCY=1
CATEGORY_CACHE_TTL_SECONDS=300
PENDING_RECLAIM_IDLE_MS=60000
CATEGORY_LOCK_SECONDS=1800
CATEGORY_PROGRESS_TTL_SECONDS=90

DIST_TRAFFIC_SCAN_LIMIT=5
DIST_TRAFFIC_VIEW_LIMIT=3
DIST_TRAFFIC_BROWSER_LIMIT=1
DIST_TRAFFIC_GLOBAL_LIMIT=8
DIST_TRAFFIC_TOKEN_SECONDS=90

TRAFFIC_SCAN_CONCURRENCY=5
TRAFFIC_VIEW_CONCURRENCY=3
TRAFFIC_BROWSER_CONCURRENCY=1
TRAFFIC_GLOBAL_CONCURRENCY=9
TRAFFIC_SCAN_MIN_INTERVAL_SECONDS=0.55
TRAFFIC_VIEW_MIN_INTERVAL_SECONDS=0.20

VIEW_COUNT_CONCURRENCY=3
VIEW_COUNT_CACHE_TTL_SECONDS=1800

DB_POOL_SIZE=3
DB_MAX_OVERFLOW=2
DB_POOL_TIMEOUT_SECONDS=30
```

Важно: `DIST_TRAFFIC_*` — общий лимит через Redis для **всех replicas вместе**. Не умножай его на количество workers.

## Views worker Variables

```env
OBSERVATION_CONCURRENCY=1
OBSERVATION_POLL_SECONDS=30
VIEW_COUNT_CONCURRENCY=3
VIEW_MEASUREMENT_REUSE_SECONDS=20
DB_POOL_SIZE=3
DB_MAX_OVERFLOW=2
```

Держи `views-worker` в одной replica. Автозамеры +3/+6/+12ч находятся в PostgreSQL и забираются этим отдельным процессом.

## Порядок перехода с v3.4.3

1. Deploy v3.5.0 сначала с текущим `bot` и `DISTRIBUTED_WORKERS=0` — это проверяет обратную совместимость.
2. Добавь Redis.
3. Создай `parser-worker`, подключи тот же `DATABASE_URL`, `REDIS_URL`, `BOT_TOKEN`, поставь `DISTRIBUTED_WORKERS=1`.
4. Создай `views-worker` с теми же тремя общими Variables.
5. Убедись по logs, что parser-worker пишет `DT PARSER worker online`, а views-worker — `DT PARSER views worker online`.
6. Только после этого переключи `bot` на `DISTRIBUTED_WORKERS=1` и redeploy.
7. Открой `/admin` → статистику: должны быть видны `Redis / distributed`, количество parser-worker и views-worker.
8. Проведи нагрузочный тест: 5 разных Telegram accounts одновременно запускают по одной категории на одинаковую и затем на разные даты.

## Что должно происходить при тесте

- У каждого пользователя Telegram отвечает сразу; polling не блокируется crawl.
- До 5 parser-worker replicas могут одновременно держать по пользовательскому scan job.
- Если два пользователя выбрали одинаковые `категория + дата + глубина`, сеть реально сканирует один worker, второй получает shared progress/result.
- 403/429 на одном worker включает Redis cooldown для всех replicas вместо того, чтобы пять контейнеров продолжали давить сайт независимо.
- Если parser-worker перезапустился посередине job, Redis Stream не теряет задание: после idle-time оно reclaim-ится другой replica.
- `/stop` работает между процессами через Redis cancel key.

## Как увеличивать мощность дальше

Сначала тестируй `parser-worker ×5`. Если всё стабильно и 403/429 мало, можно повышать количество worker replicas, **не повышая сразу DIST_TRAFFIC limits**. Это увеличит способность держать больше user jobs/очередь, но не создаст резкий burst на Kleinanzeigen.

После реальных логов уже можно отдельно подобрать `DIST_TRAFFIC_SCAN_LIMIT`, global limit и интервалы. Самый опасный вариант — просто поставить 10–20 network concurrency на каждом worker: тогда лимит фактически умножится на число replicas.
