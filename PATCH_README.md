# DT PARSER v4.22.6 — GitHub Web Patch

Base: v4.22.4 Vinted Admin Lab Workers.

This compact patch contains only runtime files changed by v4.22.5 + v4.22.6.
It intentionally excludes unchanged assets, docs, tests and history to keep GitHub web upload small.

Replace/add these files in the repository root:
- bot.py
- service_launcher.py
- vinted_lab.py
- vinted_metrics_worker.py
- vinted_browser_metrics.py (new)
- VERSION

Local helper files (not required by Railway runtime):
- capture_vinted_session.py
- CAPTURE_VINTED_SESSION_MAC.command

No manual SQL migration is required.
No new mandatory Railway variable is required for the Kleinanzeigen AutoScan patch.
VINTED_SESSION_JSON remains optional and is only for the Vinted exact-metrics browser-session path.

After replacing files, redeploy Parser. Vinted Metrics Worker should also be redeployed if you are testing the v4.22.5 exact-session path.
