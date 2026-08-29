# DT PARSER v4.15.5 — AutoScan Recovery Hardening

**Base:** v4.15.4 Organic Pipeline Correctness.

This release fixes the two remaining causes of repeat-round churn observed after the v4.15.4 funnel became transparent. It does **not** relax Organic admission and does **not** change DT Demand Score.

## 1. Date/Page UNKNOWN recovery

- `stable_fetch()` now gives persistent chronology `unknown` the same one-time fresh BrowserContext recovery previously reserved for `invalid`.
- The already-existing deterministic `sequential_locator()` is now actually invoked when exponential/binary date probes or the boundary neighborhood remain weak.
- Sequential recovery starts after the last proven `newer` page when possible, so it avoids needless page-1 rewinds.
- It remains fail-closed: if a page is still weak after retries + context recycle, the sequential pass does not claim an absent date across that gap.
- Remote Date Worker hints remain acceleration only; local verified chronology is still authoritative.

## 2. Detail Organic UNKNOWN recovery

Before a strong candidate can make an otherwise healthy category `⚠️ допроверка`, the exact detail URL now gets:

1. bounded normal HTTP attempts;
2. short retry delay for transient refusal/transport/weak/challenge responses;
3. one fresh rendered Chromium detail fetch;
4. if still UNKNOWN, one delayed final retry of **that exact candidate only**.

No lower-ranked candidate is promoted past a persistent UNKNOWN. The gate is still correctness-first and fail-closed. Proven TOP/Promo and reduced-price ads remain sticky exclusions.

## 3. UNKNOWN reason telemetry

AutoScan now aggregates exact detail UNKNOWN reasons and shows them under the funnel, for example:

`↳ причины: http_403 3 · challenge 2 · weak_document 1`

The completion notification and failed-category reason include the same breakdown. This distinguishes site pressure from parser/template problems immediately.

## 4. What stays unchanged

- Organic policy: paid visibility and reduced price are excluded.
- Exact-view completeness requirement from v4.15.4.
- Organic TOP-N backfill and fail-closed ranking.
- DT Demand Score **40 / 20 / 15 / 15 / 10**.
- Fast Sold/Lifecycle logic.
- Four user scan lanes and FIFO queue.
- Page depth: 15 per AutoScan category maximum.

## 5. Deployment

Behavior-critical: redeploy **Parser** from v4.15.5.

Recommended same-checkout consistency:
- AI Worker
- Lifecycle Worker
- Date Worker / Page Worker / View Worker

There is no manual SQL migration and no new required Railway variable. Optional tuning variables have safe defaults:

- `DETAIL_INTEGRITY_HTTP_RETRIES=2`
- `DETAIL_INTEGRITY_RETRY_DELAY_SECONDS=0.45`
- `DETAIL_INTEGRITY_BROWSER_FALLBACK=1`
- `DETAIL_INTEGRITY_BROWSER_SETTLE_MS=180`

## 6. Smoke test

1. Run a new AutoScan or retry the current failed set.
2. Confirm transient date `unknown` logs can show `Date sequential recovery ...` and either recover to `found/absent/too_deep` or remain honestly `unknown`.
3. Confirm a persistent weak date page gets one fresh BrowserContext attempt before review.
4. Confirm detail transient failures show HTTP/browser recovery logs and do not immediately fail the category.
5. Confirm a persistent detail UNKNOWN still stops ranking fail-closed.
6. Confirm admin telemetry shows exact UNKNOWN reasons when any remain.
7. Confirm Organic passed = new Radar + already-present for admitted candidates.
8. Confirm exact views remain complete for every successful category.
