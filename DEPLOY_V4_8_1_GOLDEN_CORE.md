# DT PARSER v4.8.1 — Golden Core

This build is an A/B control release.

## Parsing core
The following files are copied byte-for-byte from v4.4.0 Stability Hardening:

- `parser.py`
- `stable_engine.py`
- `traffic.py`
- `distributed.py`
- `date_manager.py`
- `date_worker.py`
- `page_manager.py`
- `page_worker.py`
- `view_manager.py`
- `view_counter_worker.py`

No performance changes from v4.8.0 remain in these files.

## What stays from the newer product branch
- RU/EN user interface and language selection
- current admin UI and worker/user scan views
- AI Lab / Product Opportunity Engine
- current commerce/database/UI features outside parsing core

## Railway test
Deploy all services from the same commit and wait for Date/Page/View workers to be online. Run one 50-page scan first, then repeat the identical scan without redeploying to compare cold vs warm workers.

This release intentionally uses the original v4.4.0 Redis worker namespaces. Do not mix old v4.4/v4.6 worker deployments with v4.8.1 at the same time.
