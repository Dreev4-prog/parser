# DT PARSER v3.6.0 — Railway Browser Workers

## Зачем

v3.6.0 делает foreground scan браузерно-изолированным: один активный пользовательский запуск обслуживается одним отдельным Chromium context внутри одного browser-worker процесса. Поиск даты и category pages идут через Playwright/Chromium, а не через общий `httpx` transport.

## Production схема

```text
bot ×1
PostgreSQL ×1
Redis ×1
browser-worker ×5
views-worker ×1
```

Все сервисы используют один и тот же `DATABASE_URL`, `REDIS_URL`, `BOT_TOKEN` и production-переменные приложения.

## 1. Обновить основной bot

Start Command:

```bash
python bot.py
```

Variables:

```env
DISTRIBUTED_WORKERS=1
REDIS_URL=${{Redis.REDIS_URL}}
DATABASE_URL=${{Postgres.DATABASE_URL}}
```

У Telegram bot должен остаться **1 экземпляр**, потому что он использует long polling.

## 2. Убрать старые parser-worker из очереди

Если у тебя запущены старые `parser-worker` из v3.5.0, останови их после деплоя 3.6.0. Иначе Redis Stream будут одновременно потреблять HTTP-workers и browser-workers.

`views-worker` оставь — он продолжает заниматься +3/+6/+12ч.

## 3. Создать browser-worker

Создай новый Railway service из того же GitHub repository.

Start Command:

```bash
python browser_worker.py
```

Основные Variables:

```env
BOT_TOKEN=...
ADMIN_IDS=...
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
DISTRIBUTED_WORKERS=1

DIST_TRAFFIC_SCAN_LIMIT=5
DIST_TRAFFIC_VIEW_LIMIT=3
DIST_TRAFFIC_GLOBAL_LIMIT=8

BROWSER_SCAN_NAV_TIMEOUT_MS=35000
BROWSER_SCAN_ACCESS_MAX_WAIT_SECONDS=45
BROWSER_SCAN_RETRY_MIN_SECONDS=6
```

`browser_worker.py` сам выставляет, если ты их не переопределял:

```env
SCAN_TRANSPORT=browser
PARSER_WORKER_CONCURRENCY=1
SHARE_ACTIVE_CATEGORY_SCANS=0
DIST_TRAFFIC_SHARED_COOLDOWN=0
TRAFFIC_SCAN_CONCURRENCY=1
TRAFFIC_BROWSER_CONCURRENCY=1
```

## 4. Сделать 5 отдельных Chromium workers

### Вариант A — replicas

У `browser-worker` поставь **5 replicas**. Каждый Railway replica является отдельным instance/container и запустит собственный процесс `python browser_worker.py`, а значит собственный Playwright/Chromium.

### Вариант B — 5 отдельных services

Если в твоём тарифе/интерфейсе replicas неудобны, просто создай:

```text
browser-worker-1
browser-worker-2
browser-worker-3
browser-worker-4
browser-worker-5
```

У всех один GitHub repo, один Start Command и одинаковые references на Redis/PostgreSQL.

Это даже удобнее для первого теста: в Railway Logs сразу видно, какой именно Chromium worker взял пользователя.

## 5. Ресурсы

Playwright/Chromium значительно тяжелее `httpx`. Не запускай 5 браузеров внутри одного маленького container. В v3.6.0 рекомендуемый профиль — **1 Chromium на 1 worker instance**.

Если worker перезапускается по OOM, сначала увеличь Memory этого service, а не `PARSER_WORKER_CONCURRENCY`.

## 6. Нагрузочный тест

После deploy:

1. Убедись, что старых HTTP parser-workers больше нет.
2. Открой Logs всех browser workers.
3. Запусти скан почти одновременно с 5 Telegram accounts.
4. В логах должны появиться 5 разных `worker id / RAILWAY_REPLICA_ID`.
5. На всех пяти Telegram-карточках при поиске даты будет строка `Отдельная Chromium-сессия`.
6. На одном worker временная ошибка не должна останавливать прогресс остальных четырёх.

## Что важно понимать

Browser isolation решает конкуренцию event loop, cookies/session state, browser lifecycle и общий глобальный cooldown из v3.5.0. Но Railway instances могут всё равно выходить в интернет через инфраструктуру одного провайдера. Разные Chromium processes **не являются обещанием разных публичных IP**.

Если все пять независимых browser-workers одновременно получают одинаковый 403/429, это уже сигнал, что ограничение находится на сетевой стороне внешнего сайта. В таком случае не нужно бесконечно повышать concurrency — надо уменьшать внешний request rate или использовать разрешённый/официальный способ доступа к данным.
