# DT Parser v4.23.2 — Vinted Radar Start Hotfix

- Исправлен «мёртвый» старт Radar: экран меняется сразу после нажатия.
- Убрана повторная попытка отвечать на уже подтверждённый Telegram callback — ошибки теперь видны прямо в сообщении.
- Добавлены этапы запуска 1/3 → 2/3 → 3/3.
- Full-market resolver сначала использует live Vinted metadata, затем последний валидный cache, затем read-only DE metadata snapshot.
- Частичный локальный fallback из нескольких категорий по-прежнему не может выдаваться за весь Vinted.
- Никаких изменений Score/Like Momentum/15-page policy/Kleinanzeigen.
