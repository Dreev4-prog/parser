# DT PARSER v4.11.7 — Free Funnel Analytics

Base: v4.11.6 Free Radar Preview.

## Goal

Measure whether the new free DT Radar preview actually leads users toward a paid subscription. The analytics are admin-only and must not change the public Radar experience or parser behavior.

## What is tracked

Only actions performed by users who do **not** currently have full access are recorded:
- opened DT Radar
- opened `Лучшие сейчас`
- opened Hot / Rising / AI Picks preview
- opened a preview product
- clicked a locked Radar feature (Search / Categories / My Radar / Records)
- clicked `Открыть полный DT Radar`

For product-preview completion the event keeps only the internal Radar product id. This makes it possible to tell whether the visitor actually opened all 5 unique demo products in a mode.

No message text, search text, external browsing history or Kleinanzeigen activity is stored by this funnel.

## Admin UI

Open:

`Админ-панель -> Бесплатные сканы -> Воронка бесплатного Radar`

The funnel shows **last 24 hours / all time**:
- Radar visitors
- `Лучшие сейчас` visitors
- users who selected a preview mode
- users who opened at least one preview product
- users who opened all 5 unique demo products in at least one mode
- users who clicked full access
- users who paid **after** their first recorded free Radar visit
- Radar -> payment conversion

The free-scans dashboard also shows a compact Radar summary: visitors, full-access clicks, purchases after Radar and conversion.

## Recent visitors

The funnel includes a paged list of recent visitors. For each user the admin sees:
- @username / first name and Telegram user id
- last Radar activity time
- number of Radar and `Лучшие сейчас` opens
- unique preview products opened in Hot / Rising / AI Picks, e.g. `🔥 5/5 · 🚀 2/5 · 🧠 0/5`
- whether full access was clicked
- free scan usage
- whether a payment happened after the free Radar visit
- which locked Radar areas were attempted

Each visitor row has a button to open the existing admin user card.

## Conversion attribution

A historical payment does not count as a Radar conversion. A user is counted as `Купили после Radar` only when a confirmed paid `SubscriptionPayment.paid_at` is at or after that user's first recorded free Radar visit in the measured cohort.

This prevents an expired former subscriber who later tries the free preview from being falsely counted as a new Radar conversion.

## Database

v4.11.7 adds one small append-only table:

`free_radar_events`

On PostgreSQL it is created with `CREATE TABLE IF NOT EXISTS` before the normal SQLAlchemy metadata pass so simultaneous Railway service startup cannot race on the new table. Indexes are created for user, event type, mode, feature, product and timestamp.

No manual migration or new Railway variable is required.

Analytics begin accumulating after v4.11.7 is deployed; v4.11.6 preview actions that happened before deployment cannot be reconstructed.

## Preserved behavior

Unchanged from v4.11.6/v4.11.5:
- free Radar preview remains 5 real products per Hot / Rising / AI Picks mode
- Search / Categories / My Radar / Records remain locked for free users
- two free parser scans remain separate
- paid Radar remains unchanged
- 84-category product-only AutoScan
- DT Score / Radar Category Feed
- Page / Date / View / AI workers
- four foreground parser lanes and FIFO queue
- subscription plans and payment providers

## Railway

Redeploy **parser**. The parser creates the analytics table automatically. Dedicated workers do not need the new feature to continue operating.

If the same repository deploy automatically rebuilds the worker services too, the `IF NOT EXISTS` table setup is safe for concurrent startup.

## Smoke test

1. Deploy parser and confirm startup reports `version=4.11.7`.
2. From a non-subscribed account open `DT Radar`.
3. Open `Лучшие сейчас -> Горячие` and open 1-2 products.
4. Click `Открыть полный DT Radar` once.
5. Open admin -> `Бесплатные сканы` and confirm the Radar visitor/click counters increased.
6. Open `Воронка бесплатного Radar` and confirm the visitor appears with the correct mode/product progress.
7. Open the same flow from a paid account and confirm it does not add free-funnel events.
8. Confirm normal parser scans, AutoScan and paid Radar work unchanged.
