# DT Parser v4.7.4 — v4.6.0 Parser Core Restore

Production-clean GitHub package.

This build uses the **exact v4.6.0 parser/worker core** for Date, Page and View collection, while retaining the newer Telegram UI, RU/EN localization, admin operations center and DT AI Lab improvements.

## Parser core

The following files are restored byte-for-byte from v4.6.0:

- `parser.py`
- `date_manager.py`
- `date_worker.py`
- `page_manager.py`
- `page_worker.py`
- `view_manager.py`
- `view_counter_worker.py`

The foreground scan orchestration in `bot.py` was also compared with v4.6.0; `process_scan_job`, `scan_worker` and `distributed_scan_worker` are identical.

All post-v4.6.0 wake-up/prewarm/idle-browser changes have been removed from the parser critical path.

## Retained user-facing features

- Russian / English UI
- language selection on first `/start`
- current admin panel and active parsing view
- DT AI Lab unread badge
- Russian AI Lab labels
- Product Opportunity Engine
- v4.6.7 Fast UI navigation

## Memory behavior

This release intentionally restores v4.6.0 runtime behavior. The 10-minute Chromium idle shutdown introduced in v4.6.1 is **not active**. Idle memory can therefore be higher than in v4.6.1+, but this gives a clean performance baseline matching the last version known to parse correctly and quickly.

## Railway

All services continue to use the same repository and root `railway.json`:

```text
python service_launcher.py
```

No new required Railway variables and no PostgreSQL migration are needed.
