# 4.21.15 Radar Equal Home UI

Base: **4.21.14 Radar 3.2 Startup Guard Fix**.

## What changed

- `▶️ НОВЫЙ СКАН` and `📡 DT RADAR 3.0` are now two equal, full-width primary actions on the active home screen.
- `🔥 Популярное` and `📊 Мои сканы` share the next row as secondary analytics/history actions.
- Trial and expired-access homes also keep Radar as its own full-width action instead of hiding it beside another button.
- The active home caption is repositioned from scan-only `Kleinanzeigen Analytics` to `DT PARSER — MARKET ANALYTICS`.
- The old `Перед новым сканом` checklist is removed from the home caption. Categories, Settings and Auto measurements remain available through their existing buttons.
- Home copy now explains both products: classic scan analytics and DT Radar 3.0 demand discovery.

## Safety / scope

This is a **Telegram UI-only release**. Existing callback IDs are preserved (`start_scan`, `radar_home`, `popular_now`, `my_scans`, etc.). Parser, Radar ranking, AutoScan, database, workers, access control, payments and view/date/page algorithms are unchanged.

## Deploy

Redeploy the **Parser** service. Worker services do not need a restart for this UI change.

## Validation

Run:

```bash
python -m compileall -q .
pytest -q
python scripts/release_smoke.py
python scripts/check_runtime_globals.py
```
