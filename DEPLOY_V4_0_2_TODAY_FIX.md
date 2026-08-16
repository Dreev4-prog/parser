# DT PARSER v4.0.2 — Today Fast Path & Partial Cache Fix

## Что исправлено

1. Частичные ScanResult больше не сохраняются в локальный/Redis cache как финальные результаты.
2. Cache namespace изменён на `v402:*`, поэтому старые v4.0.0/v4.0.1 Redis-результаты автоматически не переиспользуются.
3. Force-refresh/autorecovery удаляет старый distributed category result перед повторной попыткой.
4. Для текущей московской даты Stable Engine больше не выполняет отдельный поиск даты. Nationwide feed начинается с page 1, затем объявления фильтруются по точной дате публикации.
5. Страницы текущего дня с частично отсутствующими timestamp не превращают всю категорию в partial только из-за низкого покрытия дат.
6. Если scan всё же partial, интерфейс больше не пишет, что нулевой результат окончательный.

## Railway

Архитектуру v4.0 менять не нужно:

- bot ×1
- fleet-worker ×6
- views-worker ×1
- Redis ×1
- PostgreSQL ×1

Fleet worker start command:

```bash
python fleet_worker.py
```

Новых обязательных Variables нет.

Рекомендуемые значения:

```env
FLEET_CONTEXTS_PER_REPLICA=2
FLEET_TOTAL_SCAN_LANES=8
FLEET_TOTAL_GLOBAL_LANES=10
MIN_PAGE_DATE_COVERAGE=0.20
MIN_PAGE_DATED_ITEMS=2
MIN_DIRECTION_DATED_ITEMS=2
STABLE_WEAK_PAGE_GAP_LIMIT=3
STABLE_PAGE_RETRIES=2
```

## Проверка после deploy

Сначала проверить 1 аккаунт:
- дата: сегодня;
- 1 категория;
- 25 страниц;
- без `От просмотров` или с 0 для первого теста.

Затем 5 аккаунтов одновременно.

В сегодняшнем scan статус должен сразу перейти к сбору/стабильному проходу без отдельного длительного `Поиск даты`.
