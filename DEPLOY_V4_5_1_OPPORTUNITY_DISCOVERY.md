# DT Parser v4.5.1 — Opportunity Discovery / Clean Scan UI

v4.5.1 is the first **Opportunity Discovery** calibration of DT AI Lab. It keeps the
v4.4 parser/date/page/view core intact and changes only the shadow Early Winner logic
plus the everyday Telegram progress card.

## What AI looks for now

The goal is not TOP views. The goal is to detect a product family where **demand is
unusually strong relative to supply before the niche becomes saturated**.

Initial score (`ew-opportunity-v2`) uses:

- demand: category-balanced views/hour percentile + absolute demand floor;
- anomaly: views/hour versus a category baseline where one mass family cannot dominate
  merely by having many listings;
- supply sweet spot: strongest bonus when there is enough repeated market evidence but
  the family is not yet mass-published;
- mass penalty: historical supply can remove up to 35 Score points;
- freshness;
- price versus comparable-market median as a small supporting signal.

**Product recognition confidence no longer adds Score.** It only increases evidence
confidence. Therefore an unknown Makita/camera/coffee-machine/tool can outrank an iPhone
when its demand/supply signal is stronger.

The score reasons mark useful patterns:

- `💎 Hidden Gem` — strong demand + limited proven supply;
- `🚀 Emerging` — repeated family with strong demand before saturation;
- `⚡ Anomaly` — unusual one-off signal that needs +1/+3/+6 confirmation;
- `⚠️ Saturated` — demand exists, but mass supply reduces opportunity value.

Rarity alone is not rewarded: 1–2 historical publications receive a deliberately weak
supply factor until demand/checkpoints provide evidence.

## Diversity protection

At most **2 visible candidates from the same product cohort per scan** by default. This
prevents ten iPhones/PS5/etc. from filling the Early Winner shortlist.

The same live external listing is also suppressed from duplicate AI observation plans
for 12 hours by default, so overlapping user scans do not multiply +1/+3/+6 work.

## Market cohorts for unknown products

Reliable deterministic `identity_key` remains the first choice. When strict identity is
missing, the AI Worker creates a conservative title-family signature from the saved
PostgreSQL listing title. This is shadow analytics only; it does not alter the normal
parser identity or listing data.

Market/supply evidence is read from the existing `listings` table for the configured
lookback window. No extra Kleinanzeigen requests are used for market counting.

## Fixed scan starvation bug

v4.5.0 fetched the first 20 completed scans and only then checked which were already
analyzed. Once all first 20 had AI runs, newer scans could starve.

v4.5.1 performs `NOT EXISTS(ai_early_winner_runs)` in PostgreSQL and always takes the
oldest truly unprocessed scan.

## Clean user scan progress

Ordinary users no longer see:

- Chromium/browser session details;
- HTTP-first/browser fallback;
- date-search stage names;
- regional date pipeline/fallback;
- recovery/retry internals;
- worker routing diagnostics.

During a scan they see only the useful product progress:

```text
🔎 Сканирование · 47%
████░░░░░░

🗂 Категория · 1/2
📄 11/25 страниц
📦 326 объявлений
⏱ 2 мин
```

During exact view collection:

```text
👁 Собираю просмотры · 96%
█████████░

🗂 Категория · 1/2
📦 Объявлений: 326
👁 Проверено: 201/326
⏱ 3 мин
```

All technical detail remains in Railway logs and admin diagnostics.

## Railway

Topology stays unchanged from v4.5.0:

- Main Bot ×1
- PostgreSQL ×1
- Redis ×1
- Date Worker ×4
- Page Worker ×4
- View Worker ×4
- AI Worker ×1

No database migration is required; v4.5.1 reuses the v4.5.0 AI tables.

New optional variables (defaults are already built in):

```env
AI_MAX_PER_COHORT=2
AI_REPEAT_SUPPRESS_HOURS=12
AI_MARKET_SAMPLE_LIMIT=30000
```

Existing AI variables remain compatible:

```env
AI_EARLY_WINNER_ENABLED=1
AI_EARLY_MAX_AGE_HOURS=24
AI_EARLY_SCORE_FLOOR=65
AI_CANDIDATES_PER_CATEGORY=10
AI_TOTAL_CANDIDATES=20
AI_CONTROL_PER_CATEGORY=2
AI_CHECKPOINT_HOURS=1,3,6
AI_PAUSE_DURING_USER_SCANS=1
AI_REUSE_WINDOW_MINUTES=15
AI_MARKET_LOOKBACK_DAYS=30
```

## Safety boundary

Unchanged: exact parser, date verification, Date/Page/View workers, traffic limits,
20-minute category watchdog and user auto-measurement semantics. If AI Worker is down,
normal user parsing continues independently.
