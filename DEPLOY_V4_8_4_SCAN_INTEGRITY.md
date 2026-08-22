# DT PARSER v4.8.4 — Scan Integrity

## Что исправлено

- Неполный crawl больше не переходит в `views`.
- `views` стартует только при `request_complete=True`.
- При partial сначала работает bounded auto-recovery.
- Если recovery завершил покрытие, просмотры собираются один раз на полном проходе.
- Если recovery не смог подтвердить все участки, scan остаётся partial; подтверждённые страницы сохраняются, но Views не маскирует недостающий page coverage.

## Railway

Обновить parser, Date Worker, Page Worker и View Worker на 4.8.4. Runtime namespace остаётся совместимым с 4.8.3 (`runtime:v483`), поэтому обязательной очистки Redis между 4.8.3 и 4.8.4 нет.
