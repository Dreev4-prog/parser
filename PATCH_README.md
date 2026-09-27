# v4.23.17 — automatic PostgreSQL retention

Apply over v4.23.16 and redeploy the main `parser` service. The new maintenance
scheduler starts automatically; no additional Railway worker or variable is
required.

It removes expired Radar audit events, disposable stable-page cache, old global
view history and unreferenced cold listings in small batches. Saved user scans and
all Radar-linked listings are preserved. No manual SQL is required.

See `RELEASE_4_23_17.md` for the default retention periods and optional overrides.
