# DT PARSER v4.15.0 — DT Demand Score 2.0

**Base:** v4.14.1 Radar Price & Return UX.

This release replaces the DT AI Lab scoring formula with one demand-focused 0–100 score. It does not use Lifecycle/disappearance as a score input.

## New DT Demand Score formula

The user-facing score remains one number from 0 to 100:

- **40% Relative View Velocity** — age-matched view growth relative to the category, with a small absolute-demand stabilizer;
- **20% Acceleration** — whether current family demand is speeding up versus its own recent history and recent-vs-previous market history;
- **15% Persistence** — whether raw historical view curves keep the family above its category across independent time windows;
- **15% Repeatability** — whether multiple independent listings of the same family repeatedly show strong demand;
- **10% Price Fit** — price versus the observed market median, with the strongest bonus reserved for believable discounts rather than implausibly extreme prices.

Weights total exactly 100%.

## What changed technically

### Relative View Velocity
The old model compared views/hour across a broad category cohort. v4.15.0 prefers a similar-age cohort (very fresh listings are compared with other very fresh listings), then family-balances that cohort so many copies of one product cannot redefine the category baseline.

### Acceleration
Acceleration is based only on demand/view history. Supply and Lifecycle are not part of the score. Missing demand history is neutral instead of receiving a bonus or penalty.

### Persistence
Persistence is derived from raw `view_history` checkpoints across recent and previous market windows. One isolated spike cannot create a high persistence signal. Thin history shrinks toward neutral 0.5 and lowers confidence rather than automatically lowering the product score.

### Repeatability
The score no longer uses old AI `confirmed` labels as historical repeatability evidence. That avoided a circular feedback loop where an older model could indirectly reward its own prior decisions. Historical repeatability now comes from independent raw view-growth observations relative to category demand.

### Price Fit
A realistic discount can improve Score. Extremely low prices no longer automatically get the maximum price bonus because very large deviations can represent damaged/non-comparable/fraudulent listings and are not reliable demand evidence by themselves.

### Follow-up checkpoints
The existing +1h / +3h / +6h AI observation schedule remains unchanged in this release. At each checkpoint the score is recomputed using the same 40/20/15/15/10 structure:

- observed relative velocity;
- live acceleration/momentum;
- persistence across starting/lifetime/latest-interval demand;
- stored repeatability evidence;
- price fit.

The previous `45% initial score + 37% observed strength + 18% momentum` dynamic formula is removed.

## Lifecycle is intentionally separate

Fast Sold / Lifecycle remains available in DT Radar, but confirmed disappearance does **not** add or subtract DT Demand Score points. An ad can disappear because it sold, was manually removed, was moderated, or was fraudulent.

## UI

AI Lab keeps one primary numeric score:

`DT Demand Score: 0–100`

Saturation remains a descriptive diagnostic (`низкая / средняя / высокая`) rather than a second 0–100 score in the AI list/card.

## Compatibility

- No PostgreSQL migration.
- No new Railway variables.
- No new service.
- Existing Date/Page/View/Lifecycle worker algorithms are unchanged.
- Radar price filter and exact return context from v4.14.1 are preserved.
- Referral promo, Daily Radar, AutoScan recovery, Page Cache Recovery and four user scan lanes are preserved.

## Deployment

Redeploy at minimum:

1. **AI Worker** — required for the new scoring model;
2. **Parser** — required for the updated AI Lab wording/version.

Lifecycle Worker, Date Worker, Page Worker and View Worker can run the same repository safely; their algorithms are unchanged.

Expected AI Worker startup/heartbeat should report:

`model_version=dt-demand-score-v2`

## Smoke test

1. Redeploy Parser + AI Worker.
2. Confirm AI Worker heartbeat reports `dt-demand-score-v2`.
3. Complete a normal scan containing fresh listings with exact views.
4. Open `Админ-панель -> DT AI Lab` and confirm candidates use `DT Demand Score` wording.
5. Open a candidate and confirm the reasons include `DT Demand 2.0` with velocity/acceleration/persistence/repeatability diagnostics.
6. Let +1h / +3h / +6h observations run and confirm the Score is recalculated rather than frozen at the initial value.
7. Confirm Lifecycle Worker continues running independently and no disappearance result directly changes DT Demand Score.
