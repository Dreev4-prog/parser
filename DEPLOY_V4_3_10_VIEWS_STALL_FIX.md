# DT Parser v4.3.10 — Views Stall Fix

Replace these files in the repository:
- bot.py
- parser.py
- traffic.py
- VERSION

No new Railway variables are required. Optional defaults:
- INLINE_VIEW_PHASE_GLOBAL_LIMIT=2
- TRAFFIC_MEDIUM_LOAD_VIEW_LIMIT=6
- TRAFFIC_HIGH_LOAD_VIEW_LIMIT=4
- ACCURATE_VIEW_BROWSER_NAV_TIMEOUT_MS=15000

The 4-user scan pool and 2-category-per-job pipeline remain enabled.
