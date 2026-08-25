# DT PARSER

## v4.9.1 — View Speed Fix

Performance patch on top of the v4.9.0 Free Trial Launch. Product/trial logic,
Date Worker, Page Worker and category parsing stay unchanged. Large exact-view
batches are now guaranteed to shard across the four-replica View Worker fleet,
transient official-counter refusals get one cheap exact retry before Chromium,
and the fleet may run two verified browser fallbacks at once.

## v4.9.0 — Free Trial Launch

Stable Kleinanzeigen Telegram parser with a launch funnel for new users.

### Launch offer
- 2 free scans for never-paid users
- 1 category per free scan
- 15 / 25 pages, maximum 25
- real views, TOP-12 / TOP-50 and XLSX included
- after the free credits: subscription required for new scans

### Paid access keeps the full product
- up to 50 pages
- multiple categories
- repeat/recheck/manual view refresh
- +3 / +6 / +12h automatic measurements

### Admin
- `🎁 Бесплатные сканы` — ON/OFF without deploy
- funnel: used trial / used both / converted / conversion %
- `📣 Рассылка`
- workers / active scans / users / plans / payments / access mode

### Access and history
Expired users keep read-only access to their saved scans, TOP/history and XLSX. New parser/network work remains subscription-gated unless free-trial credits are available.

### Queue
Up to 4 user scans run simultaneously; additional scans wait in the visible FIFO queue with position/status/cancel controls.

### Stable core
v4.9.0 changes only product/access/database/UI layers. The parsing core and Date/Page/View worker protocols remain the known-good v4.8.6+ core.
