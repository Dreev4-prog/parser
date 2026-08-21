# DT Parser v4.6.0 — Product Opportunity Engine

v4.6.0 changes the core DT AI Lab philosophy: **popularity is no longer a penalty**.
The engine now separates two independent axes:

- **Opportunity 0–100** — is something unusually strong happening with demand now?
- **Saturation 0–100** — how crowded is this product family relative to other families in the same category?

A highly saturated product can still be valuable when demand accelerates versus its own history.

## Signal types

- **💎 Hidden Gem** — strong demand, repeatable signal, relatively low saturation.
- **🚀 Emerging** — demand is growing faster than supply.
- **🔥 Hot Product** — already popular/high-saturation product whose demand has accelerated versus its own recent baseline.
- **⚡ Spark** — interesting listing-level anomaly that still needs product-level confirmation.
- **⚫ Saturated** — many offers, but no fresh demand acceleration. It remains background evidence instead of being treated as a discovery.

## What changed from v4.5.1

### 1. Removed hard Mass Penalty
v4.5.1 could subtract up to 35 Score points purely because a family had many listings.
v4.6.0 does **not subtract Saturation from Opportunity Score**.

### 2. Category-relative saturation
There are no universal rules like `200 listings = mass market` anymore.
Supply is ranked against other product families inside the same category, then expressed as `Saturation 0–100`.

### 3. Own-history acceleration for popular products
When ViewHistory has enough checkpoints, the current family pace is compared with the family's own recent demand pace.
An iPhone that is simply always popular is not a new signal; an iPhone family accelerating versus itself can become **Hot Product**.

### 4. Demand vs supply momentum
The engine compares:

- recent demand trend;
- recent supply trend normalized against the whole category;
- `Demand/Supply momentum`.

If demand grows faster than supply, the signal can become **Emerging**.

### 5. Repeatability
One viral listing is no longer equal to a product pattern. The score now includes repeatability from:

- multiple same-family listings in the current scan;
- prior DT AI Lab signals/confirmed outcomes for the same cohort.

Unknown/new products are neutral: `no history` does **not** mean `rare = good`.

### 6. Admin UI
DT AI Lab now exposes:

- 💎 Hidden Gems
- 🚀 Hot / Emerging
- ⚡ Sparks / Watch
- Opportunity Score
- Saturation Score
- Demand trend
- Supply trend
- Demand/Supply momentum
- Repeatability

The ordinary user scan UI remains the clean v4.5.1 UI and still hides Chromium/date/regional/worker internals.

## Database

v4.6.0 adds only additive columns to `ai_early_winner_candidates`:

- `cohort_key`
- `opportunity_type`
- `saturation_score`
- `supply_percentile`
- `supply_growth_ratio`
- `demand_growth_ratio`
- `demand_supply_ratio`
- `repeatability`

`init_db()` performs the PostgreSQL migration automatically using `ADD COLUMN IF NOT EXISTS`; existing scans, listings and AI history are preserved.

## Railway

No new service is required. Keep the working topology from v4.5.1:

- Main Bot ×1
- Date Worker ×4
- Page Worker ×4
- View Worker ×4
- AI Worker ×1
- PostgreSQL ×1
- Redis ×1

No new mandatory Variables are required. Optional:

```env
AI_TREND_WINDOW_DAYS=7
```

`AI_MARKET_LOOKBACK_DAYS=30` remains the longer supply/history window.

## Shadow-mode recommendation

Keep DT AI Lab admin-only while it accumulates enough ViewHistory and repeated product-family evidence. The first runs will naturally have lower confidence; missing history is neutral rather than artificially rewarded or punished.
