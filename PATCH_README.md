# v4.23.0 GitHub patch

Накладывать поверх **v4.22.9**.

Изменённые файлы:

- `VERSION`
- `bot.py`
- `vinted_lab.py`
- `vinted_scan_worker.py`
- `vinted_radar.py` (new)
- `scripts/release_smoke.py`
- `tests/test_release_static.py`
- `tests/test_radar42114_startup_contract.py`
- `tests/test_vinted_4230_radar10_contract.py` (new)
- `RELEASE_4_23_0.md`

После деплоя: redeploy Parser/Bot и Vinted Scan Worker replicas. Vinted Metrics Worker для Radar 1.0 больше не нужен, но остаётся для ручного Parser режима.
