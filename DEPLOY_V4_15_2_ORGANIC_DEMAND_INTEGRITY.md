# DT PARSER v4.15.2 — Organic Demand Integrity

**Base:** v4.15.1 DT Demand Score 2.1 Evidence Adaptive.

This release fixes polluted demand inputs. DT Demand Score remains **40/20/15/15/10** and is intentionally not recalibrated here; v4.15.2 changes which listings are allowed to contribute evidence.

## Organic-only rule

A listing is permanently excluded from demand analytics as soon as DT Parser observes either of these conditions:

1. **Paid Kleinanzeigen visibility**
   - standalone `TOP` badge;
   - `Top-Anzeige` / `TopAd`;
   - `Hochgeschoben` / `Hochschieben` / bump-up;
   - `Highlight`;
   - `Galerie`;
   - explicit sponsored/promoted feature markers.

2. **Reduced / crossed-out price**
   - semantic `<del>` / `<s>` old monetary price;
   - CSS `line-through` old monetary price;
   - explicit old/original/former/previous-price classes/metadata;
   - text such as `statt 199 €`, `vorher ...`, `alter Preis ...`;
   - a real numeric price decrease observed by DT Parser between two stored observations, even if the current card template does not expose the crossed old price.

The flags are **sticky**. If a paid badge later expires or a seller changes the price again, the listing does not become a clean demand sample: its accumulated view counter has already been influenced by non-organic exposure/price intervention.

## Where dirty listings are blocked

Known non-organic listings do not contribute to:

- user scan result lists / TOP ranking;
- fresh exact-view collection;
- future `ViewHistory` demand checkpoints;
- DT AI initial candidates;
- DT AI +1/+3/+6 observations;
- Relative View Velocity cohorts;
- Acceleration / Persistence / Repeatability evidence;
- Price Fit market cohorts;
- DT Radar signals, price statistics and category feeds;
- Lifecycle/Fast Sold enrollment.

The AI Worker also applies defensive DB filters, so historical raw ViewHistory belonging to a now-flagged listing cannot train DT Demand Score.

## Sticky integrity registry

New additive table:

`listing_integrity`

It stores the Kleinanzeigen `external_id` plus sticky `is_promoted` / `is_price_reduced` flags. This is necessary because a TOP card may be rejected before a normal `listings` row is ever created. If the same ad later reappears without the badge, the registry still blocks it from organic analytics.

`listings` also receives additive column:

`is_price_reduced BOOLEAN DEFAULT FALSE`

Both are created automatically by `init_db()`. No manual SQL migration is required.

## Existing Price Drop feature is preserved

Reduced-price listings are excluded from demand scoring but their raw Listing / PriceHistory data may remain stored. The existing `📉 Снижение цены` export can still use that history. Organic Radar/AI and normal scan outputs do not use those rows.

## Cleanup of already polluted Radar / AI history

At parser startup v4.15.2 runs an idempotent Organic Demand cleanup:

- historical numeric downward price steps in `price_history` mark the listing as reduced;
- AI candidates/observations/events for known dirty listings are removed;
- dirty Radar snapshots and Radar listing associations are removed;
- dirty Lifecycle watches are removed;
- affected Radar product families are rebuilt from surviving clean snapshots/listings;
- a product family with no clean evidence left is removed from Radar.

The raw Listing/PriceHistory/ViewHistory audit data is not destructively deleted; analytical queries refuse flagged rows.

Historical paid promotions that were never recorded as `is_promoted` by an older release cannot be reconstructed from the database alone. If such an ad is still shown with a paid marker, the next scan detects it, makes the flag sticky and purges its old analytical contribution immediately.

Expected cleanup log when applicable:

`Organic Demand cleanup dirty=... ai=... radar_snapshots=... radar_links=... lifecycle=...`

## Cache integrity

v4.15.2 changes result-card filtering semantics, so old page caches must not be replayed:

- Redis Page Worker cache uses schema `v4152-organic`;
- stable PostgreSQL page-checkpoint payloads use schema `v4152-organic` and old payloads are rejected.

No manual Redis clear is required.

## Parser telemetry

Category logs now report both exclusions separately:

- `promoted=...`
- `reduced_price=...`

The standalone `TOP` badge is detected conservatively as a badge/label; ordinary listing titles such as `TOP Zustand Fahrrad` are not rejected merely because they contain the word TOP.

## DT Demand Score is unchanged

v4.15.2 retains v4.15.1 exactly:

- 40% Relative View Velocity
- 20% Acceleration
- 15% Persistence
- 15% Repeatability
- 10% Price Fit
- evidence-adaptive normalization
- Lifecycle/disappearance outside the score

This release improves the **truth of the inputs**, not the weights.

## Railway deployment

Redeploy these services from the same v4.15.2 checkout:

1. **Parser** — DB migration, sticky registry, startup cleanup and scan integrity;
2. **Page Worker** — new TOP/reduced-price parsing and cache schema;
3. **Date Worker** — uses the shared category-page parser and should stay on the same parsing semantics;
4. **AI Worker** — organic-only training/candidate/checkpoint queries;
5. **Lifecycle Worker** — receives the defensive non-organic race guard from `radar.py`.

The **View Worker algorithm is unchanged**. Redeploying it from the same repository is safe but not required for behavior.

No new Railway variable is required. Do not copy main-parser stable-mode variables to helper workers.

## Smoke test

1. Open/scan a category containing a paid standalone `TOP` card. Logs should increase `promoted=...`; that external ID must not appear in the scan result or Radar.
2. Use a normal title containing the word `TOP` but no paid badge. It must remain eligible.
3. Find a card with a crossed old price. Logs should increase `reduced_price=...`; the listing must not receive exact views or enter Radar/AI.
4. Re-scan a previously flagged external ID after its badge/crossed price disappears. It must remain excluded via `listing_integrity`.
5. Check AI Worker after new scans: candidates and market cohorts must contain only `is_promoted=FALSE AND is_price_reduced=FALSE` listings.
6. Check Radar after startup cleanup: previously known dirty snapshots should disappear/rebuild while clean product-family history remains.
7. Verify `📉 Снижение цены` still works from stored price history.
