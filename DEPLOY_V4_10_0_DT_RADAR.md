# DT PARSER v4.10.0 — DT Radar

## What this release adds

v4.10.0 keeps the v4.9.1 Four-Lane Queue Guarantee and adds **DT Radar** — a global, persistent knowledge base of strong product families found by DT Parser and DT AI.

Radar is not another per-user TOP. All successful scans feed one shared analytical database. Similar listings are grouped into one product family when deterministic identity/family recognition allows it.

## User flow

The main menu now contains:

```text
📡 DT Radar
```

Active-access users can open:

- 🔥 Горячие сейчас
- 🚀 Набирают обороты
- 🧠 AI Picks
- 🏆 Лучшие за всё время
- 🗂 Категории
- ⭐ Мой Radar

Expired/free-trial users see a locked Radar teaser with global counts and a subscription CTA; they do not receive the product list.

## What enters Radar

Two independent signal sources feed the same product record:

1. **Completed scan TOP** — up to TOP-12 listings with verified real view counts from every complete saved scan.
2. **DT AI** — every non-control Early Winner / Product Opportunity candidate. Initial AI score and every later AI observation update the same Radar product.

A scan merge is DB-only and is started in the background after the user scan is finished. It does not add Kleinanzeigen requests and does not delay the result card. AI observations reuse the existing AI/View Worker architecture.

## Product grouping

Radar prefers, in order:

1. deterministic `identity_key` with confidence >= 70;
2. AI `cohort_key` / deterministic family key;
3. one listing-specific fallback key when no safe family can be recognized.

This means repeated Apple TV / console / tool listings can accumulate under one product family instead of flooding Radar with duplicates.

## Persistent history

New PostgreSQL tables:

- `radar_products` — one persistent product family and its live/peak score;
- `radar_product_listings` — distinct listings ever associated with that product;
- `radar_snapshots` — append-only score/signal history;
- `radar_favorites` — per-user `⭐ Мой Radar` watch list.

Radar product rows and snapshots have **no automatic delete path**. A product can cool from Hot to Historical, but it remains searchable/listed historically and keeps its Peak Score.

## DT Score lifecycle

Each product stores:

- current DT Score;
- Peak Score;
- confidence;
- status;
- AI opportunity type;
- signal count;
- AI confirmed count;
- distinct listing count;
- best views / best views-per-hour;
- observed price range;
- latest reason/source;
- first/last Radar timestamps.

Statuses:

```text
🔥 hot
🚀 rising
✅ stable
💤 cooling
🗄 historical
```

Fresh AI/scan signals can increase or decrease the live score. If no fresh signal arrives, an hourly DB-only maintenance pass gradually cools the current score after 72 hours. The product itself and its historical Peak Score are never removed.

## Historical backfill

After deployment the main bot starts a background, idempotent one-time backfill:

- all existing non-control AI candidates are merged into Radar;
- all existing complete saved scans contribute their historical TOP real-view products;
- an `app_settings` marker prevents the full backfill from rerunning on every deploy.

The bot starts polling before the backfill begins, so an old database does not block Telegram availability during migration.

## Existing parser architecture retained

The following parser/traffic engines are unchanged from v4.9.1:

- `parser.py`
- `stable_engine.py`
- `distributed.py`
- `traffic.py`
- `date_manager.py` / `date_worker.py`
- `page_manager.py` / `page_worker.py`
- `view_manager.py` / `view_counter_worker.py`

The four main user lanes from v4.9.1 are retained: users 1–4 run, user 5+ waits FIFO. Free-trial and paid scans share the same four lanes.

## Railway deployment

Deploy the v4.10.0 code to:

- `parser` (required for UI, scan-to-Radar merge, backfill, score maintenance)
- `AI Worker` (required so future AI score changes flow into Radar)

Date/Page/View worker code is unchanged. Redeploying those helpers is optional if Railway deploys all services from the same repository automatically.

No new Railway variable is required.

No destructive PostgreSQL migration is required. SQLAlchemy creates the four new additive Radar tables automatically.

## Smoke test

1. Deploy parser + AI Worker from v4.10.0.
2. Open `📡 DT Radar`; old strong products should begin appearing after the background backfill starts.
3. Complete a new scan with real views; its TOP products should appear in Radar without delaying the scan result.
4. Open a Radar item and verify Current Score, Peak Score, category, listing/signal counts and score history.
5. Add/remove it from `⭐ Мой Radar`.
6. Let AI Worker analyze a new scan; AI Picks / product score should update from the AI signal.
7. Confirm the main parser still starts four `scan-worker-*` tasks and a fifth user waits FIFO.
