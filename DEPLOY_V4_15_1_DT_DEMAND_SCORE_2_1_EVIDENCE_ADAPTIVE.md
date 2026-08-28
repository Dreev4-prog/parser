# DT PARSER v4.15.1 — DT Demand Score 2.1 Evidence Adaptive

**Base:** v4.15.0 DT Demand Score 2.0.

This release fixes the main calibration problem discovered in live Radar rounds: fresh products with exceptional view growth could be mechanically capped near the observation band because future/history factors that did not yet exist were still voting as neutral `0.5` values.

## Formula stays the same

The single user-facing DT Demand Score remains:

- **40% Relative View Velocity**
- **20% Acceleration**
- **15% Persistence**
- **15% Repeatability**
- **10% Price Fit**

Lifecycle/disappearance remains completely outside the score.

## Evidence Adaptive normalization

v4.15.1 changes *when* a factor is allowed to vote.

Unknown evidence is no longer inserted as a synthetic 50% result. Instead, only factors with real evidence are included in the numerator and denominator, and their available base weights are renormalized to 100%.

Example for a genuinely fresh listing:

- Velocity = 95%, available
- Acceleration = unknown
- Persistence = unknown
- Repeatability = unknown
- Price Fit = 80%, available

The score is calculated from the available 40 + 10 base weight only:

`(40×0.95 + 10×0.80) / 50 = 92%`

The missing 50 base-weight points do not push the listing toward 50/100. As +1/+3/+6h checkpoints and more independent listings arrive, acceleration, persistence and repeatability automatically join the same 40/20/15/15/10 model.

## Evidence activation rules

### Velocity
Active when the category/age-matched comparison cohort has at least two observations.

### Acceleration
Initial score: active only when the product has real own/history demand-growth evidence. At follow-up checkpoints it becomes active from measured post-baseline growth.

### Persistence
Initial score: active after at least two independent historical raw demand-rate observations. Follow-up score: active once enough real elapsed time exists to compare the starting and subsequent demand behavior.

### Repeatability
Initial score: active when at least two comparable current listings exist or at least two independent historical raw demand observations exist. Missing repeatability does not qualify a product as a Hidden Gem by itself.

### Price Fit
Active only when the listing has a price and the product family has at least three observed market listings supporting a median. One isolated price cannot move the score.

## Relative Velocity safety gate

Evidence normalization makes Velocity much more important for new products, so v4.15.1 also hardens the Velocity factor against tiny-category false positives.

Relative percentile still dominates, but it is multiplicatively gated by absolute view volume / views-per-hour. A listing with 3 views when its peers have 1 view can no longer become an 88+ signal merely because it ranks first in a tiny cohort. A genuinely strong fresh listing with meaningful absolute traffic can still reach 90+ immediately.

Synthetic regression check used for this release:

- tiny cohort top listing: 3 views / 30 min -> about **73**, not 88+;
- strong fresh outlier: 80 views / 30 min in a normal fresh cohort -> about **94**, even with no future/history evidence yet.

## Follow-up checkpoints

+1h / +3h / +6h remain unchanged as a schedule. They continue to recompute the same single DT Demand Score, now with evidence-adaptive weights.

Observed velocity and acceleration become real evidence immediately. Persistence joins after enough elapsed evidence. Repeatability and Price Fit vote only when their raw evidence is actually present.

## Diagnostics

AI reasons now include:

`DT Demand 2.1: ...`

and an internal Evidence Adaptive line showing which components were actually available and how much of the base 100 weight had real evidence. This is for calibration/debugging; the user-facing product remains one DT Demand Score.

Expected AI Worker model version:

`model_version=dt-demand-score-v2.1-evidence-adaptive`

## Compatibility

- No PostgreSQL migration.
- No new Railway variables.
- No new service.
- No Lifecycle influence on DT Demand Score.
- Radar price filter / return context from 4.14.1 retained.
- Fast Sold Lifecycle from 4.14.0 retained.
- Referral promo, Daily Radar, AutoScan recovery, Page Cache Recovery and four user scan lanes retained.

## Deployment

Redeploy at minimum:

1. **AI Worker** — required for the new scoring behavior;
2. **Parser** — keep the whole deployment on the same v4.15.1 release/version.

Date/Page/View/Lifecycle worker algorithms are unchanged.

## Live validation

Do not judge the release from old 4.15.0 completed candidates alone. Existing AI history is intentionally preserved. Compare new runs whose `AIEarlyWinnerRun.model_version` is `dt-demand-score-v2.1-evidence-adaptive`.

For the next 2–3 Radar circles check:

1. how many initial candidates land in 80–89 and 90+;
2. whether obvious low-volume tiny-category outliers stay below strong-signal levels;
3. whether 80+/90+ products continue to show real view growth at +1/+3/+6;
4. confirmation rate after enough observations finish.

The target is not to maximize the number of 90+ items. The target is to stop suppressing genuinely exceptional fresh demand while keeping weak relative-only noise out.
