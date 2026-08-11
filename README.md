# Kleinanzeigen Parser Bot v3.0.0

v3.0 adds deterministic **product recognition** on top of the fast direct public view-counter, saved scans, Moscow-date search and view-velocity history.

## v3.0 — Product Identity

Every parsed listing is now classified locally (no external AI/API call) into structured fields when the title is clear enough:

- brand
- product type
- model
- important variant
- storage
- RAM where relevant
- model-defining specs such as MacBook display/chip or Apple TV generation
- confidence score
- stable `identity_key` used by Smart Analytics

High-confidence rules currently cover the most useful resale/electronics families, including:

- Apple: iPhone, iPad, MacBook Air/Pro, Mac mini, iMac, Apple TV, AirPods
- Sony: PS5/PS4 variants, PlayStation Portal, DualSense / DualSense Edge
- Nintendo Switch / Switch 2
- Xbox Series / One
- Steam Deck, Meta Quest
- Samsung Galaxy, Google Pixel
- NVIDIA RTX/GTX and AMD RX GPUs
- AMD Ryzen and Intel Core CPUs
- conservative generic brand/model fallback for other products

Examples that stay separate:

- `PS5 Slim Disc 1TB` vs `PS5 Slim Digital 1TB`
- `iPhone 15 Pro Max 256GB` vs `iPhone 15 Pro 128GB`
- `MacBook Pro 14 M3 Pro 18GB/512GB` vs another RAM/storage configuration
- `Apple TV 4K Gen 3 128GB Ethernet` vs 64GB/older generation

Unknown or weak titles are **not forced** into a strong group. Smart market/frequency analytics use the structured identity only when confidence is high enough, then fall back conservatively.

## Telegram UI

Saved scan cards now include `🧠 Модели` / `🧠 Распознанные модели`:

- recognition coverage for the scan
- number of distinct recognized product configurations
- grouped model/configuration
- listing count
- median price
- maximum public views
- recognition confidence

Top-by-views and View Velocity screens also show the normalized identity when available.

## CSV

Normal listing CSVs now include:

- `🧠 Распознанный товар`
- `Бренд`
- `Модель`
- `Версия`
- `Память, GB`
- `RAM, GB`
- `Точность распознавания, %`

Existing title/price/views/date/link fields are preserved.

## Existing databases

`init_db()` adds the v3.0 identity columns automatically. On startup, listings collected by older versions are backfilled locally from their existing titles, so saved v2.8/v2.9 scans can immediately benefit from the new model view.

## Existing features preserved

- fast direct public view counter with browser fallback
- promoted/Top/Highlight listing filter
- exact target-date scan in Moscow time
- 25 / 50 / 100 page depth
- multi-user queue and shared category cache
- saved `Мои сканы`
- manual view refresh
- view history and 1/3/6/24h velocity
- CSV export and Smart Analytics modes

## Railway

No new required variables. Existing variables remain valid.

Start command:

```bash
python bot.py
```

The existing Playwright Docker image/setup remains unchanged for browser fallback.
