# DT PARSER v4.15.6 — Bump Resurrection Integrity

**Base:** v4.15.5 AutoScan Recovery Hardening.

v4.15.6 closes the remaining paid-visibility hole shown by live Kleinanzeigen ads that carry the purple circular up-arrow (`Hochschieben`) but no text badge that older detection recognized. It also prevents an already-known external ID from re-entering Organic Radar after its displayed publication day jumps forward.

## 1. Purple Hochschieben icon detection

Search-card and live detail-page parsing now inspect semantic SVG/icon attributes in addition to visible text/classes:

- `bumpup` / `bump-up`;
- `hochschieb*`;
- `push-up` / `pushup`;
- paid feature/boost tokens;
- up-arrow icon tokens only when they sit inside an explicit promotion/visibility feature context.

A generic navigation `arrow-up` is **not** enough to classify an ad as promoted. `TOP Zustand` in a title is still allowed.

## 2. Same-external-ID resurrection detection

`listings.first_posted_date_msk` stores the earliest publication day DT can defend for an external ID and never moves forward.

If the same external ID later appears with a newer `posted_date_msk`, or DT has a database `first_seen_at` earlier than the newly claimed publication day, the chronology is impossible for a genuinely new listing. The ad becomes sticky promoted with reason:

- `resurfaced_posted_date_shift`, or
- `resurfaced_after_first_seen`.

This does **not** use a high view count as proof. A genuinely viral fresh listing is not rejected merely for having many views.

## 3. Sticky reason registry

`listing_integrity` receives additive `promotion_reason VARCHAR(80)`.

Examples:

- `search_promotion_marker`;
- `bump_icon`;
- `promoted_dom_marker`;
- `promoted_metadata`;
- `resurfaced_posted_date_shift`;
- `resurfaced_after_first_seen`.

Once promoted, the external ID stays excluded from Organic Demand even after the paid marker disappears.

## 4. Existing Radar is cleaned, not grandfathered

On the first Parser startup after 4.15.6:

1. all currently visible Radar families are temporarily quarantined (`organic_verified_at = NULL`);
2. Telegram startup is not blocked by the network sweep;
3. Radar maintenance re-checks every current Radar association with the new live detail gate plus resurrection chronology;
4. a proven paid/reduced listing is stickily marked and the existing targeted purge removes its Radar snapshots, product association, AI candidate/observations and Lifecycle/Fast Sold watch;
5. clean historical families are marked `bump_sweep_verified_at` after their old associations pass the sweep, but **remain quarantined**;
6. a fresh v4.15.6 AutoScan/user/AI signal resets the family's pre-v4.15.6 snapshots/links and rebuilds it from demand-safe evidence before `organic_verified_at` is restored;
7. an UNKNOWN detail result stays quarantined instead of being guessed organic and is retried by maintenance.

The sweep is idempotent and persisted with AppSetting flags.

## 5. Organic baseline for unknown pre-DT history

New additive Listing fields:

- `organic_baseline_views`;
- `organic_baseline_at`;
- `organic_history_status`;
- `first_posted_date_msk`.

Radar also receives `bump_sweep_verified_at`, which records completion of the one-time historical bump check **without** making legacy score/history visible.

Raw counters remain stored unchanged for audit/UI. Demand ranking uses two modes:

- a clean listing first observed on its displayed publication day may use its fresh verified total after the bump gate;
- an older/ambiguous listing gets a baseline first; its first inherited total is not used as Organic Radar/AI velocity. After a second clean observation, demand uses only the DT-observed delta above that baseline.

This is intentionally different from `many views = promoted`: view volume alone never creates a sticky promotion flag.

## 6. Cache integrity

Because card promotion semantics changed, stale parsed page payloads must not be replayed:

- Redis Page Worker schema: `v4156-bump-resurrection`;
- stable PostgreSQL page payload schema: `v4156-bump-resurrection`.

No manual Redis clear is required.

## 7. Existing Organic rules remain

Still excluded:

- TOP / Top-Anzeige;
- Hochschieben;
- paid Highlight;
- paid Galerie;
- sponsored/promoted visibility;
- crossed/reduced-price listings.

DT Demand Score weights remain **40 / 20 / 15 / 15 / 10**. Fast Sold remains outside the score.

## 8. Database migration

`init_db()` adds all columns automatically. No manual SQL is required.

No new Railway variable is required.

## 9. Railway deployment

Redeploy from the **same v4.15.6 checkout**:

1. **Parser** — required: DB migration, resurrection detection, quarantine + historical Radar sweep;
2. **Page Worker** — required: new icon detector + cache schema;
3. **Date Worker** — required/recommended: shares card parsing and stable payload schema;
4. **AI Worker** — required: Organic Baseline-aware initial velocity;
5. **Lifecycle Worker** — required/recommended for one strict integrity version;
6. **View Worker** — algorithm unchanged; same-checkout deploy recommended for version consistency.

## 10. Expected first-deploy behavior

Immediately after Parser starts, the old Radar may temporarily show fewer or zero families because the pre-4.15.6 base is quarantined before the network sweep. This is intentional correctness-first behavior.

Logs include:

`v4.15.6 quarantined existing Radar pending bump-resurrection integrity sweep`

and then:

`v4.15.6 bump-resurrection Radar sweep: {'products': ..., 'checked': ..., 'clean': ..., 'dirty': ..., 'unknown': ..., 'sweep_verified': ...}`

Dirty families disappear permanently from Organic Radar. Sweep-clean legacy families remain hidden until a **fresh v4.15.6 signal** rebuilds them; this prevents old accumulated totals from being silently trusted again. The sweep runs at background traffic priority and later retries only families whose historical sweep is still unresolved.

## 11. Smoke tests

1. A card/detail containing an SVG/use token such as `icon-feature-bumpup` must be promoted.
2. A generic navigation `icon-arrow-up` outside a promotion context must remain clean.
3. `TOP Zustand Fahrrad` without a paid badge must remain clean.
4. Same external ID with `first_posted_date_msk=2026-08-13` and current `posted_date_msk=2026-08-29` must become sticky promoted.
5. A current Radar listing proven bumped must lose its Radar snapshots/link and Lifecycle watch.
6. A clean legacy family must get `bump_sweep_verified_at` but keep `organic_verified_at=NULL` until a fresh strict signal arrives.
7. That fresh strict signal must reset pre-v4.15.6 snapshots/links and then restore `organic_verified_at`.
8. An UNKNOWN sweep result must remain quarantined, not organic.
9. A history-unknown listing gets one baseline; only a later observed delta may train velocity.
10. Exact-view completeness and v4.15.5 Date/Detail recovery remain unchanged.
11. DT Demand Score weights remain 40/20/15/15/10.
