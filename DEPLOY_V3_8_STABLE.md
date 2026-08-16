# DT PARSER v3.8.0 — Stable Scan Engine / Railway

## Что изменилось

v3.8.0 меняет сам принцип сканирования даты:

- один и тот же `категория + дата + глубина` теперь является общей работой между пользователями;
- вместо jump/binary-поиска даты используется последовательная проверка хронологии страниц;
- каждая подтверждённая страница сохраняется как PostgreSQL checkpoint;
- граница даты сохраняется в `stable_date_index`;
- слабая страница повторяется отдельно до 3 раз, а не заставляет начинать всю категорию заново;
- recovery использует подтверждённые страницы из PostgreSQL даже после рестарта/другого worker;
- обычный foreground scan больше не собирает просмотры inline; baseline выполняет `views-worker` сразу после сохранения скана, затем +3/+6/+12 ч;
- если пользователь включил фильтр `От просмотров`, foreground scan всё ещё собирает нужные значения, иначе фильтр невозможно применить корректно;
- ручная «Допроверка» больше не является нормальной частью интерфейса Stable Engine.

Новые таблицы создаются автоматически через SQLAlchemy `create_all`:

- `stable_page_checkpoints`
- `stable_date_index`
- `stable_category_jobs`

Старые таблицы и пользовательские данные не удаляются.

---

## Рекомендуемая Railway-схема

```text
bot              ×1
stable-worker    ×5
views-worker     ×1
PostgreSQL       ×1
Redis            ×1
```

Старые `parser-worker`, `browser-worker` и `hybrid-worker` на время теста лучше выключить, чтобы они не забирали те же Redis jobs.

### bot

Start Command:

```bash
python bot.py
```

### stable-worker

Создай новый Railway service из того же GitHub repository.

Start Command:

```bash
python stable_worker.py
```

После успешного запуска поставь **5 replicas**. Внутри каждой replica локальная concurrency = 1.

### views-worker

Start Command:

```bash
python views_worker.py
```

Одна replica на первом тесте достаточна.

---

## Общие Variables

У `bot`, `stable-worker` и `views-worker` должны указывать на те же PostgreSQL и Redis:

```env
BOT_TOKEN=...
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
DISTRIBUTED_WORKERS=1
```

Для `stable-worker` критические настройки уже принудительно включены самим `stable_worker.py`, чтобы старые Railway Variables из v3.6/v3.7 не выключили Stable Engine:

```env
SCAN_TRANSPORT=hybrid
STABLE_SCAN_ENGINE=1
SHARE_ACTIVE_CATEGORY_SCANS=1
PRIMARY_SCAN_INLINE_VIEWS=0
DIST_TRAFFIC_SHARED_COOLDOWN=0
```

Поэтому удалять старую переменную `SHARE_ACTIVE_CATEGORY_SCANS=0` необязательно для stable-worker — entry point её переопределит. Но для чистоты Variables её лучше убрать.

Дополнительные значения по умолчанию:

```env
STABLE_PAGE_RETRIES=3
STABLE_PAGE_RETRY_SECONDS=1.2
STABLE_PAGE_CHECKPOINT_TTL_SECONDS=300
STABLE_DATE_INDEX_TTL_SECONDS=900
SCAN_AUTO_RECOVERY_PASSES=3
SCAN_AUTO_RECOVERY_DELAY_SECONDS=2
STABLE_SCAN_LANES=5
STABLE_GLOBAL_LANES=8
```

На первом тесте их лучше не увеличивать.

---

## Как работает один скан

Для свежей даты вместо прыжков `1 → 2 → 4 → 8 → binary search` worker делает:

```text
page 1 → verified
page 2 → verified
page 3 → target date found
page 4 → target date
...
older date → stop
```

Если `page 7` временно слабая:

```text
page 7 attempt 1 ✗
page 7 attempt 2 ✗
page 7 attempt 3 ✓
```

Страницы 1–6 не сканируются заново.

После успешной страницы её полный разобранный результат сохраняется в PostgreSQL. Поэтому следующий recovery / другой depth / другой Railway worker может получить:

```text
PostgreSQL checkpoint → без внешнего запроса
```

---

## Что происходит с одинаковыми пользователями

Если одновременно запущены:

```text
User A → Компьютеры / 16.08 / 25
User B → Компьютеры / 16.08 / 25
User C → Смартфоны / 16.08 / 25
```

A становится владельцем общей работы `Компьютеры/16.08/25`, B подписывается на её прогресс и получает тот же результат, а C независимо сканирует смартфоны.

Если глубина разная (например 25 и 50), это разные jobs, но подтверждённые страницы хранятся по категории/дате/feed/page, поэтому второй job переиспользует уже готовые первые страницы из PostgreSQL.

---

## Просмотры

Для стабильности сетевой этап страниц и этап view counters разделены.

Обычный scan заканчивает сбор объявлений без сотен дополнительных запросов к карточкам. После сохранения `views-worker` создаёт immediate baseline (`target_hours=0`), а затем план:

```text
+3h
+6h
+12h
```

Baseline не отправляет отдельное уведомление пользователю. Контрольные +3/+6/+12 продолжают работать как раньше.

Если в настройках пользователя `От просмотров > 0`, counters собираются foreground, потому что без них нельзя честно сформировать результат с этим фильтром.

---

## Первый нагрузочный тест

1. Запусти `stable-worker ×5`.
2. Убедись, что старые parser/browser/hybrid workers остановлены.
3. С пяти Telegram-аккаунтов выбери по одной категории и 25 страниц.
4. Нажми запуск максимально близко по времени.
5. В интерфейсе должно быть `Стабильный проход`, а не длительный jump/binary `Поиск даты`.
6. Повтори те же категории второй раз: в Railway logs должны появляться `checkpoint=True`, а число внешних запросов заметно уменьшиться.
7. В `/admin → База и парсинг` смотри блок **Stable Scan Engine**: общие jobs, partial и количество PostgreSQL checkpoints.

Если после этого отдельные workers всё ещё получают массовые `403/429`, это уже внешний отказ сайта, а не очередь/поиск даты. Stable Engine не пытается обходить явный отказ; он сохраняет подтверждённую работу и продолжает только после разрешённых повторов.
