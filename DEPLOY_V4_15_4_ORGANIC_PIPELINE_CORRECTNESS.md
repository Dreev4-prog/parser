# DT PARSER v4.15.4 — Organic Pipeline Correctness

**Base:** v4.15.3 Strict Organic Radar Gate.

v4.15.4 fixes the full AutoScan → exact views → Organic Gate → Radar pipeline after live telemetry showed a healthy page crawl producing almost no Radar signals. The Organic policy is **not loosened**: paid visibility and reduced-price listings remain excluded. The release fixes the places where clean listings were silently lost before Radar and completes the strict detail-page admission contract.

## 1. Root cause fixed: no 24-item exact-view recovery cap

Older AutoScan behavior tried the cheap official visit counter for all clean listings, but only a bounded 24 unresolved URLs were sent through the exact recovery path. Every other miss was written as `view_count = NULL`, and Radar later selected only rows with non-null views. A category could therefore be marked successful while almost all clean listings were invisible to Radar.

v4.15.4 now:

1. tries the cheap official counter for every clean target-date listing;
2. sends **every unresolved URL** through the dedicated exact View Worker/browser recovery path;
3. persists only verified exact counters;
4. if even one target remains unknown, marks that category `⚠️ допроверка` for Radar and does **not** rank an incomplete view population.

When Redis is configured, the dedicated View Worker fleet is enabled by default unless `REMOTE_VIEW_WORKER_ENABLED=0` is explicitly set. If the fleet is expected but has no live heartbeat, AutoScan does not open hundreds of heavy local fallbacks inside the main parser: unresolved views remain unknown and the category is retried later.

This is correctness-first: an unknown listing could be the real TOP-1, so an incomplete population must not be presented as an exact ranking.

## 2. Real live detail-page Organic Gate

Search-card filtering remains the cheap first line. Before a candidate can enter Radar, v4.15.4 also opens the exact public `/s-anzeige/...` detail page and proves:

- final canonical listing ID matches the requested `external_id`;
- no paid `TOP` / `Top-Anzeige` marker;
- no bump/`Hochgeschoben` paid marker;
- no paid Highlight / Galerie feature marker;
- no sponsored/promoted marker;
- no crossed/old/reduced price UI.

Normal wording such as `TOP Zustand` does not count as a paid badge. A normal photo-gallery control labeled `Galerie` also does not count as paid Galerie without a paid-feature class/data marker.

`403`, `429`, challenge HTML, wrong redirect, wrong listing identity, unavailable/weak document and transport failures are **UNKNOWN**, never organic.

## 3. Sticky dirty verdict stays strict

A detail page that proves paid visibility or a reduced price immediately updates both:

- `listings.is_promoted` / `listings.is_price_reduced`;
- sticky `listing_integrity`.

The affected listing is then purged from AI/Radar/Lifecycle analytics and the affected Radar family is rebuilt from surviving clean evidence. Raw Listing / PriceHistory / ViewHistory audit history remains available.

## 4. Correct Organic TOP-N backfill

The old implementation could stop after the raw first 12 candidates. v4.15.4 walks the exact-view ranking in order:

- proven promoted/reduced candidates are skipped;
- the next ranked candidate is checked;
- this continues until up to **12 verified organic** positions are admitted or the ranked list ends.

There is no artificial `rows[:12]` or 24/48 correctness cutoff.

If a higher-ranked candidate gets an **UNKNOWN** detail verdict, the pipeline stops fail-closed and the category goes to `⚠️ допроверка`. It does **not** fill lower positions past an unknown candidate, because that candidate may actually belong in the Organic TOP-12.

## 5. Retry idempotency

A retry chain reuses the parent AutoScan round ID for Radar source keys. Radar snapshots already committed before a later transient UNKNOWN are treated as existing idempotent successes, not new repeatability evidence. A brand-new manual/daily round still creates fresh observations.

Admin telemetry separates:

- new Radar signals;
- signals already present from the parent retry round.

## 6. Legacy Radar quarantine is now real

New additive column:

`radar_products.organic_verified_at TIMESTAMP NULL`

All families created before v4.15.4 begin unverified (`NULL`) and are hidden from user-facing Radar lists/search/categories/counters until a new strict live-detail signal certifies them.

On the first strict certification of a legacy family, pre-gate Radar snapshots, associations and Lifecycle watches for that family are reset, while the stable product ID/favorites remain. The family is rebuilt from strict v4.15.4 evidence.

This is intentional: old data is not automatically trusted merely because it existed before the new gate.

## 7. Transparent AutoScan funnel

The admin AutoScan screen now shows the hidden stages that previously looked like `8369 listings → +3 Radar`:

- clean target-date listings;
- search-card TOP/Promo exclusions;
- search-card / historical price-reduction exclusions;
- exact views verified / requested;
- exact-view Radar candidates;
- detail pages checked;
- Organic passed;
- detail TOP/Promo blocked;
- detail reduced-price blocked;
- detail UNKNOWN;
- new Radar signals;
- idempotent already-present signals during retry.

A category with incomplete views or an unknown higher-ranked detail candidate is no longer counted as `✅ Успешно` for Radar.

## 8. What does NOT change

DT Demand Score remains exactly:

- **40% Relative View Velocity**
- **20% Acceleration**
- **15% Persistence**
- **15% Repeatability**
- **10% Price Fit**

Evidence-Adaptive normalization remains unchanged. Lifecycle/Fast Sold remains outside DT Demand Score. Fast Sold still requires a strong fresh Radar signal and confirmed disappearance logic; it simply receives cleaner, more complete Radar input after this fix.

Page/date chronology, the private-seller search filter, four user scan lanes and FIFO queue remain unchanged.

## 9. Database / Railway

`init_db()` adds `radar_products.organic_verified_at` automatically. No manual SQL is required.

No new Railway variable is required.

Behavior-critical deploy from the **same v4.15.4 checkout**:

1. **Parser** — required: full AutoScan pipeline, telemetry, DB migration, Radar admission;
2. **all View Worker replicas** — strongly required for complete exact-view recovery under load;
3. **AI Worker** — required so AI → Radar uses the same live Organic Gate;
4. **Lifecycle Worker** — required/recommended for one strict Radar/Lifecycle version.

Page Worker and Date Worker parsing algorithms are unchanged from v4.15.3, but deploying them from the same checkout is recommended for one-version consistency.

### Important for an in-progress v4.15.3 circle

Do **not** continue an old v4.15.3 circle after deploying this release. Stop the current circle, deploy v4.15.4, then start a **new full AutoScan round**. The old round already processed categories with the incomplete view-recovery semantics and its aggregate counters cannot be retroactively made trustworthy.

## 10. Smoke test

After deploy, run a fresh AutoScan and verify:

1. For every successful category, `Точные просмотры` reaches `verified/requested`. If it does not, that category must appear under `⚠️ допроверка` and must not claim a complete Radar ranking.
2. `Чистых объявлений даты` remains much larger than the Organic TOP candidates; it is the clean input population, not the number that should enter Radar.
3. A normal clean detail page passes and the category fills up to 12 organic positions when enough candidates exist.
4. A paid TOP candidate is stickily blocked and the next proven candidate backfills its slot.
5. A crossed old price is stickily blocked and the next proven candidate backfills its slot.
6. `TOP Zustand ...` without a paid badge remains eligible.
7. A normal `Galerie` photo-control does not trigger paid Galerie by text alone.
8. A wrong detail redirect or page containing the requested ID only in recommendations is rejected as `wrong_identity`.
9. A 403/429/challenge on a higher-ranked candidate makes the category `⚠️ допроверка`; lower candidates are not silently promoted past it.
10. Repeating only failed categories does not create duplicate repeatability signals for candidates already committed in the parent round.
11. Legacy pre-v4.15.4 Radar families remain hidden until a new strict signal certifies them.
12. DT Demand Score model/weights remain 40/20/15/15/10.

Expected healthy production pattern is no longer a mysterious `thousands → 3`. The exact counts depend on category demand and true non-organic evidence, but every reduction stage is now visible and fail-closed.
