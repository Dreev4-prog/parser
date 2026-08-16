# DT PARSER v4.0.0 — Railway Browser Fleet

## Идея

В этой версии Railway используется как настоящий пул вычислений:

- `bot` — 1 replica, только Telegram/UI/платежи/очередь;
- `fleet-worker` — до 6 replicas на Hobby;
- каждый `fleet-worker` держит **один долгоживущий Chromium**;
- внутри Chromium по умолчанию создаются **2 независимых BrowserContext**;
- Redis раздаёт пользовательские scan jobs между всеми browser lanes;
- PostgreSQL хранит результаты/checkpoints;
- `views-worker` остаётся отдельным и не занимает foreground browser lanes.

Стартовый профиль:

```text
6 fleet-worker replicas
× 2 BrowserContext на replica
= до 12 одновременно активных browser contexts

Глобальный сетевой потолок по умолчанию: 8 scan requests одновременно.
```

Контекст — не вкладка другого пользователя: cookies/storage изолированы. При завершении scan job контекст закрывается, а Chromium остаётся прогретым для следующей задачи.

## Почему это быстрее старого browser-worker

Старый вариант создавал отдельный Chromium runtime для каждого parser job. В v4.0 один Chromium процесс переиспользуется внутри конкретного Railway replica, а задачи получают новые isolated contexts. Это экономит старт браузера и RAM, но не смешивает пользовательские cookies.

## Railway topology

```text
bot            ×1
fleet-worker   ×6
views-worker   ×1
Redis          ×1
PostgreSQL     ×1
```

Старые `stable-worker`, `hybrid-worker`, `browser-worker`, `parser-worker` одновременно с fleet-worker не запускай — они потребляли бы ту же Redis очередь.

## Настройка fleet-worker

Создай/переиспользуй worker service из того же GitHub repository.

Start Command:

```bash
python fleet_worker.py
```

Общие references/variables:

```env
BOT_TOKEN=...
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
DISTRIBUTED_WORKERS=1
```

Fleet variables для первого теста:

```env
FLEET_CONTEXTS_PER_REPLICA=2
FLEET_TOTAL_SCAN_LANES=8
FLEET_TOTAL_GLOBAL_LANES=10
```

Сам entry point принудительно включает:

```env
SCAN_TRANSPORT=browser
STABLE_SCAN_ENGINE=1
SHARED_BROWSER_RUNTIME=1
PRIMARY_SCAN_INLINE_VIEWS=0
SHARE_ACTIVE_CATEGORY_SCANS=0
DIST_TRAFFIC_SHARED_COOLDOWN=1
```

`SHARE_ACTIVE_CATEGORY_SCANS=0` сделан намеренно: одновременно запущенный медленный scan одного пользователя не удерживает второго пользователя как subscriber. Завершённые результаты/checkpoints всё равно переиспользуются.

## Реплики и ресурсы

Для первого production теста:

- replicas: **6**;
- `FLEET_CONTEXTS_PER_REPLICA=2`;
- ориентир по лимиту памяти: **2 GB на replica**;
- ориентир CPU limit: **2 vCPU на replica**, если текущие настройки Railway позволяют;
- сначала не ставь 3 contexts/replica.

Playwright/Chromium требователен к памяти. Если один replica стабильно держится существенно ниже лимита RAM, потом можно тестировать:

```env
FLEET_CONTEXTS_PER_REPLICA=3
```

но только после проверки Metrics.

## Важный глобальный limiter

12 contexts не означают, что все 12 должны одновременно делать внешний request. По умолчанию:

```env
FLEET_TOTAL_SCAN_LANES=8
FLEET_TOTAL_GLOBAL_LANES=10
```

Если 403/429 почти нет и latency нормальная, тестируй `10/12`. Если растут timeouts/refusals — возвращай 6–8. v4.0 использует общий Redis circuit breaker: кластер снижает давление при отказах вместо того, чтобы каждый browser replica повторял запросы независимо.

## Static outbound IP

Не включай Railway Static Outbound IP специально ради browser fleet. У Railway static outbound IP применяется к исходящему трафику всех replicas сервиса. И без static IP нельзя считать, что каждая replica гарантированно получит уникальный публичный IP. Fleet создан для CPU/RAM/изоляции/параллельности, а не для обхода ограничений сайта.

## Проверка после deploy

Начни с:

1. 6 replicas `fleet-worker`;
2. 5 аккаунтов;
3. на каждом по одной категории и 25 страниц;
4. нажать старт почти одновременно.

В Railway logs каждого replica должна появляться строка:

```text
Railway browser fleet runtime started | replica=...
```

а parser-worker log показывает `local_concurrency=2` и `transport=browser`.

Затем тест:

- 10 аккаунтов × 1 категория × 25 страниц;
- после этого 5 аккаунтов × 2–3 категории;
- только затем 50/100 страниц.

## Если два пользователя всё равно долго стоят

Смотри не CPU, а три метрики:

1. RAM/CPU конкретных replicas;
2. HTTP 403/429/timeouts;
3. PostgreSQL/Redis latency.

Если CPU/RAM свободны, а ответы сайта тормозят, увеличение browser contexts не поможет — тогда bottleneck внешний. Если CPU/RAM забиты, добавление replicas или снижение contexts/replica даст предсказуемый эффект.
