# DT PARSER v4.3.21 — PAGE WORKER + 180s SCAN CACHE

## Что добавлено

- Отдельный Railway **Page Worker** для сетевой загрузки страниц после того, как основной стабильный parser уже нашёл выбранную дату.
- Один Page Worker replica держит **2 изолированных BrowserContext** по умолчанию.
- Рекомендуемый старт: **2 replicas** = до 4 параллельных page lanes.
- Redis page cache: **180 секунд**.
- Page-level single-flight: два пользователя, которые почти одновременно просят одну и ту же страницу, не запускают два одинаковых сетевых запроса.
- Если Page Worker offline / не успел / вернул ошибку, основной бот автоматически использует прежний локальный page fetch.
- Поиск даты, `parser.py`, `traffic.py`, View Worker и View Sharding v4.3.20 не менялись.

## Как работает

1. Main bot локально и прежним стабильным алгоритмом находит первую страницу выбранной даты.
2. После этого upcoming страницы отправляются в Redis Page Queue.
3. Page Worker replicas параллельно прогревают Redis cache.
4. Main bot проходит страницы в прежнем хронологическом порядке и получает готовые `CategoryPageInfo` из cache.
5. Через 180 секунд page cache автоматически исчезает.

Важно: кэш содержит только краткоживущий результат страницы. PostgreSQL по-прежнему является постоянным хранилищем сканов/checkpoints.

## Railway — новый сервис

Создай ещё один service из **того же GitHub repo**.

Название сервиса:

```text
Page Worker
```

При таком имени общий `service_launcher.py` автоматически запускает:

```text
python page_worker.py
```

### Variables Page Worker

Обязательно:

```text
REDIS_URL=${{Redis.REDIS_URL}}
```

Для максимальной надёжности определения роли можно дополнительно задать:

```text
DT_SERVICE_ROLE=page-worker
```

`DATABASE_URL`, `BOT_TOKEN`, payment variables Page Worker не нужны.

### Replicas

Рекомендуемый первый режим:

```text
2 replicas
```

Каждая replica по умолчанию:

```text
PAGE_WORKER_CONCURRENCY=2
```

Итого: два Chromium процесса (по одному на replica) и до четырёх изолированных page contexts.

## Main bot

Если в main bot уже есть `REDIS_URL`, новых обязательных Variables нет.

Page Worker включается автоматически, когда heartbeat найден. Для ручного kill-switch:

```text
REMOTE_PAGE_WORKER_ENABLED=0
```

## Default settings

```text
PAGE_CACHE_TTL_SECONDS=180
PAGE_REMOTE_TIMEOUT_SECONDS=150
PAGE_REMOTE_STALL_SECONDS=25
PAGE_PREFETCH_ENABLED=1
PAGE_PREFETCH_MIN_PAGES=4
PAGE_PREFETCH_EXTRA_PAGES=3
PAGE_WORKER_CONCURRENCY=2
PAGE_WORKER_HEARTBEAT_SECONDS=3
PAGE_WORKER_JOB_TIMEOUT_SECONDS=90
PAGE_WORKER_RECLAIM_IDLE_MS=120000
```

Менять их на первом тесте не нужно.

## Проверка

В Telegram:

```text
/admin → 📄 Page Worker
```

Ожидаемо:

```text
🟢 online · workers: 2
Кэш страниц: 180 сек.
```

Во время одного скана на 25/50 страниц должны расти counters у обеих Page Worker replicas.

Повтор такого же category/page запроса в течение примерно 3 минут должен показать cache reuse и сделать меньше реальных page fetches.

## Rollback

Самый быстрый rollback без удаления Railway service:

```text
REMOTE_PAGE_WORKER_ENABLED=0
```

Main bot сразу продолжит работать старым локальным путём.
