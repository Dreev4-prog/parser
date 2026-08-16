from __future__ import annotations

"""Resource-efficient Browser -> HTTP parser worker for Railway.

Each Railway replica processes one user scan at a time. The scan opens Chromium only
long enough to establish a normal public browser session, transfers its storage state
to a lightweight Playwright APIRequestContext, closes Chromium, and performs the bulk
category work over HTTP. Compatibility failures may briefly relaunch Chromium, while
explicit 403/429 refusals are always honored and never bypassed by switching transport.
"""

import os

# Configure before parser/bot imports because production settings are read at import.
os.environ.setdefault("SCAN_TRANSPORT", "hybrid")
os.environ.setdefault("PARSER_WORKER_CONCURRENCY", "1")
os.environ.setdefault("SHARE_ACTIVE_CATEGORY_SCANS", "0")
# Keep refusal cooldown local to the affected replica so one session cannot freeze
# every user's progress. Cross-replica scan/global concurrency is still governed by Redis.
os.environ.setdefault("DIST_TRAFFIC_SHARED_COOLDOWN", "0")
os.environ.setdefault("TRAFFIC_SCAN_CONCURRENCY", "1")
os.environ.setdefault("TRAFFIC_BROWSER_CONCURRENCY", "1")

# v3.7.1: hybrid replicas must have independent foreground network lanes.
# Older Railway Variables could leave DIST_TRAFFIC_SCAN_LIMIT at 2/3 and make
# some replicas look frozen. A dedicated hybrid profile deliberately wins over
# those generic legacy values while remaining tunable through HYBRID_* vars.
os.environ["DIST_TRAFFIC_SCAN_LIMIT"] = os.getenv("HYBRID_SCAN_LANES", "5")
os.environ["DIST_TRAFFIC_GLOBAL_LIMIT"] = os.getenv("HYBRID_GLOBAL_LANES", "8")

from parser_worker import main  # noqa: E402


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
