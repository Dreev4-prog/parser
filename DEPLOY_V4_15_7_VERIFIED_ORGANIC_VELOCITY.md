# DT PARSER v4.15.7 — Verified Organic Velocity

**Base:** v4.15.6 Bump Resurrection Integrity.

v4.15.7 removes the last way an inherited/unknown view total can dominate DT Radar or DT Demand Score. A large counter is **not** treated as proof of paid promotion. Instead, DT separates the counter it inherited before it started observing the ad from growth it personally verified afterwards.

DT Demand Score weights stay exactly **40 / 20 / 15 / 15 / 10**.

## 1. Hard first-observation rule: 400+

The threshold is fixed in code at **400 views** and applies only to the **first exact counter DT observes for that external ID**.

- first DT observation `0..399` on a genuinely fresh clean listing may continue using the normal fresh-total logic;
- first DT observation `>=400` becomes an **untrusted Organic Baseline**;
- the inherited baseline contributes **zero** views to Radar ranking and **zero** Relative View Velocity to DT Demand Score;
- high view count alone never sets `is_promoted` and never creates a sticky promotion verdict.

Examples:

- `399 -> 520`: the listing was first seen below the threshold; its later crossing of 400 does not invalidate already observed demand;
- first seen at `400`: baseline only;
- first seen at `942`: baseline only;
- first seen at `16,337`: baseline only.

This prevents a suspected old/bumped/reposted ad from beating a genuine fresh winner merely because it already carried a large counter when DT discovered it.

## 2. Two clean checkpoints are mandatory for 400+ baselines

A 400+ baseline moves through:

1. `high_baseline` — inherited total stored, no Score vote;
2. after the first later clean exact measurement (minimum 30 minutes later): `high_check_1` — still no Score vote;
3. after the second later clean exact measurement (another minimum 30 minutes later): `observed` — the listing is certified for **delta-only** demand scoring.

Same-batch retries or measurements less than 30 minutes apart cannot masquerade as separate checkpoints. A counter rollback also does not count as clean growth evidence.

Once certified:

`demand_views = current_exact_views - organic_baseline_views`

and elapsed time is measured from `organic_baseline_at`, not from the inherited publication total.

Example:

- 11:00 baseline: `942` -> contributes `0`;
- 11:30 checkpoint #1: `1002` -> still contributes `0`;
- 12:00 checkpoint #2: `1077` -> certified delta `+135` over 60 minutes;
- DT evaluates `+135 / hour`, never `1077 / hour`.

## 3. Automatic low-priority verification

Parser runs a lightweight **Verified Organic Velocity scheduler** for pending 400+ baselines.

- user scans always have priority;
- before **each** checkpoint the exact listing detail page must pass the live Organic Gate again;
- only then does the checkpoint use the existing exact View Worker/browser recovery path;
- only recently seen active listings (last seen within 24h) are auto-checked, so old library rows cannot create a background traffic storm;
- pending ads are checked in small batches;
- a failed counter request is retried later instead of being converted into organic evidence;
- no separate Railway service is required.

This avoids the dead end where a 400+ ad is withheld correctly but never receives the later measurements needed to prove a genuine hit.

## 4. Verified winners can re-enter Radar

After checkpoint #2, DT does not blindly restore the old total.

The newly verified listing is placed into a category/age-matched cohort built only from **demand-safe** view metrics. `score_initial_rows()` calculates the existing evidence-adaptive DT Demand Score from that verified delta. A dedicated `verified_velocity` Radar signal is emitted only when the resulting Score is at least **72**.

Therefore:

- an inherited `16,337` with weak later growth stays out;
- an inherited `942` that then genuinely gains `+135/hour` can still surface;
- all scoring uses observed delta, not the inherited total.

## 5. Defensive gates across Radar and AI

`demand_safe_metric()` is now the shared authority for view provenance.

Radar scan TOP, AutoScan TOP and AI initial candidate scoring all refuse a pending 400+ baseline. `record_ai_candidate()` also defensively rejects an old/stale AI candidate if its Listing is currently high-baseline pending.

Historical ViewHistory demand cohorts only use listings whose latest provenance state is `trusted`/`observed`; pending high baselines cannot quietly influence Persistence/Repeatability market baselines before certification.

## 6. Cleanup of 4.15.6 high-total scores

The first Parser startup on v4.15.7 performs an idempotent repair for clean listings whose stored first Organic Baseline is already `>=400`:

- their provenance is reset to `high_baseline` with **0** certified checkpoints;
- old AI candidates/observations based on inherited totals are removed;
- their Lifecycle/Fast Sold watches are removed;
- affected Radar product families are quarantined with `organic_verified_at = NULL`.

The listing is **not** marked promoted merely because it had 400+ views.

When a later clean demand-safe signal reaches an affected Radar family, the existing strict family-reset path deletes the old snapshots/associations and rebuilds the family from current verified evidence. This prevents pre-v4.15.7 high-total snapshots from silently becoming visible again.

## 7. AutoScan telemetry

The AutoScan admin screen now distinguishes:

- exact counters collected;
- `Initial >=400` listings waiting for two checkpoints;
- high-baseline listings whose delta is already verified;
- demand-safe candidates actually eligible for Radar ranking;
- normal detail Organic Gate results.

Typical line:

`Initial >=400: 17 ждут 2 замера · delta verified: 4`

Those 17 are not lost and are not called promoted; they are simply prevented from influencing Score until DT has direct evidence.

## 8. Database migration

`listings` receives additive fields automatically:

- `organic_verified_checkpoints INTEGER DEFAULT 0`;
- `organic_last_checkpoint_at TIMESTAMP`;
- `organic_last_checkpoint_views INTEGER`.

Existing v4.15.6 fields remain:

- `organic_baseline_views`;
- `organic_baseline_at`;
- `organic_history_status`.

No manual SQL migration is required.

No new required Railway variable is required.

## 9. What does NOT change

- Organic paid-visibility detection from v4.15.6 (purple Hochschieben icon, TOP, Highlight, paid Galerie, sponsored markers);
- same-external-ID resurrection detection;
- sticky promotion/reduced-price integrity registry;
- exact view-count acceptance rules;
- Date/Page chronology and v4.15.5 recovery;
- four foreground user scan lanes/FIFO;
- DT Demand Score weights: **40% Relative View Velocity / 20% Acceleration / 15% Persistence / 15% Repeatability / 10% Price Fit**;
- Lifecycle remains outside DT Demand Score.

## 10. Railway deployment

Behavior-critical deploy from the **same v4.15.7 checkout**:

1. **Parser** — required: DB migration, startup cleanup, 400+ scheduler, Radar telemetry;
2. **AI Worker** — required: shared demand-safe initial scoring and historical cohort filtering;
3. **Lifecycle Worker** — recommended/required for one-version Radar/Lifecycle model;
4. **Page Worker + Date Worker** — parsing semantics are unchanged from v4.15.6; same-checkout deployment is recommended for version consistency;
5. **View Worker** — algorithm unchanged, but it supplies the exact low-priority checkpoints and should remain healthy.

A fresh full AutoScan after deployment is recommended. Existing affected Radar families are quarantined automatically, so old high-total scores do not need a manual database delete.

## 11. Smoke tests

1. First exact counter `399` on a same-day clean listing -> demand-safe total may be used normally.
2. The same listing later reaches `500` -> it is **not** reclassified merely for crossing 400 later.
3. First exact counter `400` -> `high_baseline`, 0 checkpoints, no Radar/AI view score.
4. First exact counter `942` -> same behaviour.
5. First follow-up at +30m must first pass the live detail Organic Gate; then checkpoint=1, still no demand metric.
6. Second follow-up at +60m must pass the live detail Organic Gate again; then checkpoint=2, status `observed`, metric is `current - baseline` only.
7. Two retries within a few minutes must not count as two checkpoints.
8. A counter decrease must not certify organic velocity.
9. A verified high-baseline listing with weak delta must stay out of `verified_velocity` Radar signal (Score <72).
10. A verified high-baseline listing with genuinely strong category-relative delta may enter Radar, with reasons explicitly showing baseline/delta.
11. Pre-v4.15.7 AI candidates and Lifecycle watches tied to clean 400+ baselines are removed on first startup; affected Radar families are quarantined.
12. TOP/Hochschieben/reduced listings stay sticky-excluded exactly as before.
13. DT Demand Score remains 40/20/15/15/10.
