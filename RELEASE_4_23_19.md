# DT Parser 4.23.19 — balanced Radar navigation

This patch removes the database imbalance introduced by the 4.23.18 UI cache.

- Opening Radar no longer starts `radar_stats()` in the background. That exact
  full-catalogue aggregate remains available to the scheduled daily digest only.
- Product feeds and Radar search fetch one look-ahead row for pagination instead
  of running an exact full-result `COUNT` before every page.
- Telegram category and product callbacks are acknowledged before database work,
  so the client does not keep showing a loading spinner.
- PostgreSQL retention defaults to every 15 minutes instead of every 5 minutes
  and skips a pass while scan/AutoScan traffic is active.

The patch does not change AutoScan admission, observations, DT Score, HOT/Rising
thresholds, product visibility rules, saved scans or retention periods. No
database migration and no Railway variable are required.

Deploy `bot.py`, `radar.py`, `db_retention.py` and `VERSION` together.
