# DT PARSER v4.15.3 — Strict Organic Radar Gate

**Base:** v4.15.2 Organic Demand Integrity.

v4.15.3 closes the last admission gap between a clean-looking Kleinanzeigen search card and DT Radar. Search-card filtering remains the cheap first line, but **every new Radar signal now needs a live, verifiable detail-page organic verdict before it is allowed into Radar**.

DT Demand Score is unchanged: **40 / 20 / 15 / 15 / 10**.

## 1. Fail-closed Radar admission

Before `_upsert_signal()` can write a scan-hot, AutoScan or AI Radar signal, DT Parser now:

1. re-reads `listings` + sticky `listing_integrity` from PostgreSQL;
2. fetches the exact public `/s-anzeige/...` detail page;
3. proves that the returned document belongs to the requested external ID;
4. checks the detail body for paid visibility and reduced/old-price UI;
5. re-reads sticky integrity under the same per-listing PostgreSQL advisory lock immediately before the Radar write.

The signal is **not accepted** when the detail request is ambiguous: 403, 429, challenge page, unavailable page, wrong redirect, missing listing identity or transport failure. Unknown is never converted into “clean”. A later scan/checkpoint can try again.

This gate runs for Radar candidates only — not for every parsed listing — so normal category page collection, Date Worker and View Worker algorithms remain unchanged.

## 2. Detail-page non-organic detection

The final gate blocks and permanently flags the listing when the live detail page exposes either:

- `TOP` / Top-Anzeige;
- Hochschieben / bumped placement;
- Highlight;
- Galerie;
- explicit promoted/sponsored/advertisement feature markers;
- a semantic `<s>` / `<del>` previous monetary price;
- CSS/class/metadata old-price or line-through UI;
- explicit `statt`, `vorher`, `alter Preis`, `ursprünglich` old-price text.

The detail detector reuses the conservative v4.15.2 rules: normal wording such as `TOP Zustand` in the listing title is not enough by itself to reject the ad.

The Radar detail gate is strict even if an old Railway environment accidentally still has `FILTER_PROMOTED_LISTINGS=0`.

## 3. Sticky dirty verdict + immediate purge

If the detail page proves paid visibility or a reduced price:

- `listings.is_promoted` / `listings.is_price_reduced` is made sticky;
- `listing_integrity` receives the same sticky verdict;
- existing AI candidates/observations for that listing are purged;
- Radar snapshots / product links for that listing are purged;
- Lifecycle watches for that listing are purged;
- affected Radar product families are rebuilt from surviving clean evidence.

Raw Listing / PriceHistory / ViewHistory audit rows are still preserved, so the existing `📉 Снижение цены` feature remains intact.

## 4. Legacy Radar quarantine

A new additive field is created automatically:

`radar_products.organic_verified_at TIMESTAMP NULL`

All Radar families created before v4.15.3 start with `NULL`. They are **quarantined from user-facing Radar lists, search, category counts and Radar totals** until a new signal for that family passes the live detail gate.

When a legacy family gets its first verified-clean v4.15.3 signal, its old pre-gate Radar snapshots, Radar listing associations and Lifecycle watches are reset for that family, then the family is rebuilt from strict verified evidence. This prevents an old unknown snapshot from influencing a newly certified product score or price range.

No raw parser history is deleted by this quarantine.

## 5. Organic TOP-N backfill

A detail gate can reject a candidate that ranked inside the first 12 by raw views. v4.15.3 therefore keeps a bounded reserve of up to `limit × 4` ranked candidates and continues until it saves up to the requested number of **organic** Radar products.

For the standard Radar TOP-12 this means at most 48 candidates are available as fallbacks, while healthy categories normally stop after the first 12 clean detail checks.

## 6. Cross-process race protection

Parser and AI Worker are separate Railway processes. v4.15.3 uses a PostgreSQL advisory lock keyed by external ID for the final integrity re-check and signal insert. A concurrent sticky dirty verdict cannot be silently overwritten by a stale detached `Listing` object.

The existing Radar product-family advisory lock remains in place.

## 7. What does not change

- DT Demand Score: **40% Relative View Velocity / 20% Acceleration / 15% Persistence / 15% Repeatability / 10% Price Fit**;
- Evidence-Adaptive normalization from v4.15.1;
- v4.15.2 sticky search-card integrity registry;
- category parser depth and date logic;
- exact official view-count rules;
- Page / Date / View Worker concurrency architecture;
- four user scan lanes and FIFO queue;
- Lifecycle remains outside DT Demand Score.

## 8. Database migration

`init_db()` adds `radar_products.organic_verified_at` and its PostgreSQL index automatically. No manual SQL and no new Railway variable are required.

## 9. Deployment

Minimum behavior-critical redeploy from the same v4.15.3 checkout:

1. **Parser** — user scan Radar merge, AutoScan Radar merge, DB migration and strict gate;
2. **AI Worker** — AI-to-Radar signals must use the same strict gate.

Recommended for one-version consistency:

3. **Lifecycle Worker** — same Radar/model code.

Page Worker, Date Worker and View Worker algorithms are unchanged. Redeploying them from the same repository is safe but not required for the v4.15.3 Radar-gate behavior.

## 10. Smoke test

1. Deploy v4.15.3 and open Radar before a new clean signal arrives. Legacy families without `organic_verified_at` must not be shown.
2. Trigger a scan containing a normal clean detail page. Its Radar candidate should pass, receive `organic_verified_at`, and appear normally.
3. Use a listing whose detail page shows a purple `TOP` marker. It must be blocked even if the search card did not expose TOP.
4. Use a listing whose detail page shows `6.950 €` plus crossed `19.000 €`. It must be blocked and `is_price_reduced=TRUE` must remain sticky.
5. Make the detail request return/imitate 403, 429 or challenge HTML. The signal must be skipped — never treated as clean.
6. Verify a title like `TOP Zustand Fahrrad` without a paid badge or crossed price. It must remain eligible.
7. For a category where one of the raw TOP-12 candidates is blocked, verify Radar continues to the reserve and still fills the next organic slot when possible.
8. Confirm DT Demand Score model/version and weights remain unchanged.
