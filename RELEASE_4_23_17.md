# DT Parser 4.23.17 — bounded PostgreSQL storage

This patch adds automatic, bounded PostgreSQL retention to the main `parser`
service. No extra Railway worker is required.

Default policy:

- Radar checkpoint audit events: 7 days.
- Stable page/date cache: 24 hours.
- Global raw `view_history`: 30 days. Per-user `scan_view_history` is untouched.
- Unreferenced listings not seen for 60 days: removed in small batches together
  with their global price/view history.
- Listings referenced by a saved user scan, Radar, lifecycle tracking or legacy
  analytics are preserved.
- `listing_integrity` is always preserved.

The scheduler starts 45 seconds after the bot, then runs every five minutes.
Deletes are intentionally split into small transactions so normal parsing can
continue and PostgreSQL autovacuum can reuse the freed pages.

Optional Railway variables:

```text
DB_RETENTION_ENABLED=1
DB_RETENTION_INTERVAL_SECONDS=300
RADAR_EVENT_RETENTION_DAYS=7
STABLE_CACHE_RETENTION_HOURS=24
VIEW_HISTORY_RETENTION_DAYS=30
COLD_LISTING_RETENTION_DAYS=60
```

Important: normal `DELETE` plus autovacuum stops/reduces future volume growth by
reusing space, but Railway's displayed volume may not immediately shrink. A
one-time `VACUUM FULL` is optional and should only be run during a maintenance
window after a backup because it locks the table.
