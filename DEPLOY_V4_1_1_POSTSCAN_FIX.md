# DT PARSER v4.1.1 — Post-scan crash fix

## Что исправлено

Production-лог показал, что сам скан Heimwerken за 16.08.2026 успешно прошёл страницы 1–25 (`relation=target`, `verified=True`, `valid=True`), но затем процесс падал при сохранении служебного `CategoryScanState`:

`NameError: name 'berlin_date_key' is not defined`

В v4.1.1 helper восстановлен. Дополнительно запись `CategoryScanState` сделана нефатальной: ошибка служебного checkpoint-summary больше не превращает уже успешно собранный скан в `partial`.

## Railway

Схема остаётся прежней:
- bot ×1
- fleet-worker ×6
- views-worker ×1
- Redis ×1
- PostgreSQL ×1

Новых Variables и миграций PostgreSQL нет. Все сервисы должны быть на одном commit v4.1.1.

## Проверка

Сначала: 1 аккаунт → 1 категория → сегодня → 25 страниц → без ограничения по просмотрам.

В финальном логе ожидается `complete=True`, а `Queue scan error ... NameError: berlin_date_key` больше быть не должно.
