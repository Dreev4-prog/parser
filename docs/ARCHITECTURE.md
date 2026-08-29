# DT Radar Core 2.0 — Architecture

## 1. Foreground priority

User scans always have priority. AutoScan pauses before a new category when foreground work is active/queued. While AutoScan owns foreground priority, parser-side background integrity/checkpoint browser/view traffic is paused so it cannot invert locks with the Detail Gate.

A category has a bounded watchdog. Hard Stop cancels the in-flight AutoScan category, resets its browser context and keeps that category index for a clean resume.

## 2. Fresh Layer — today

Every normal/manual/daily Fresh round scans up to 15 verified target-date pages per eligible product category.

Pipeline:

`Date/Page chronology -> card integrity -> exact views -> view provenance -> live Organic Gate -> unified 48H scoring -> Demand Gate -> Radar`

A category with incomplete exact views or unresolved higher-ranked Organic Gate evidence is fail-closed and goes to retry instead of fabricating a TOP.

## 3. Context Layer — yesterday

At most once per Moscow calendar day, after a completed manual/daily Fresh round, DT scans yesterday with the same 15-page cap and same integrity pipeline.

Context has two jobs:

1. enrich durable Listings/ViewHistory/PriceHistory evidence for Persistence, Repeatability, price profiles and demand history;
2. allow genuinely proven yesterday listings to compete in the **same 48H Radar**.

Context does not mean “trust yesterday's total”. A row with unknown provenance is withheld. A qualified yesterday row must have demand-safe views and pass the same age-aware Demand Gate/live Organic Gate as a today row.

## 4. View provenance

First exact DT observation:

- `0..399`: can be a trusted total only if the listing is genuinely fresh/clean; ambiguous older history remains baseline-only;
- `>=400`: always an untrusted baseline.

For a high baseline, two later clean exact measurements at least 30 minutes apart are required. Then:

`demand_views = current_exact_views - baseline_views`

The inherited total never votes in DT Score or the Demand Gate.

## 5. Relative age cohorts

Relative View Velocity uses non-overlapping comparable-age cohorts:

- `0–3h`
- `3–6h`
- `6–12h`
- `12–24h`
- `24–48h`

A 2-hour listing and a 30-hour listing may both appear in the same public Radar, but each receives its Relative Velocity percentile from its own age cohort.

## 6. Absolute Demand Gate

Relative ranking alone is not proof of demand. The live classification layer requires cumulative **demand-safe** evidence:

`30 / 40 / 60 / 80 / 100` views across the five age cohorts.

- Hot = Score `>=72` + confidence `>=45` + 100% gate;
- Strong = Score `>=65` + confidence `>=35` + 60% gate;
- Early = Score `>=58` + 25% gate.

Below Early, the signal has zero live Radar Rank.

The gate is conservative over time: without a new view measurement, demand stays frozen while evidence age advances. This allows a stalled early signal to downgrade automatically.

## 7. DT Demand Score and Radar Rank

Public DT Score remains fixed and evidence-adaptive:

- 40% Relative View Velocity
- 20% Acceleration
- 15% Persistence
- 15% Repeatability
- 10% Price Fit

There are no extra signal-count/confirmation bonuses after the model.

The internal ordering layer is separate:

`Radar Rank = 70% DT Score + 20% Evidence Confidence + 10% Evidence Maturity`

`RadarProduct.current_score` is the actual DT Score of its current representative signal; `peak_score` is historical.

## 8. Organic Integrity

Sticky exclusions remain for TOP, Hochschieben (including purple bump icon), paid Highlight/Galerie/sponsored markers, reduced/crossed-out prices and impossible same-ID resurfacing chronology.

A positive dirty verdict purges that listing's Radar/AI/Lifecycle evidence. Unknown is never treated as organic.

## 9. Aggregate product consistency

A product family can contain multiple listings, but its live score/status/rank/confidence/reason are taken from one coherent current representative snapshot. Confidence from one listing is never blended with demand from another to manufacture Hot.

Only admitted evidence inside the current 48H window may represent a live product. Historical snapshots remain available for audit/Peak history.

## 10. Cache/runtime integrity

Audited builds use `v4200-core2-audit3` for Page/Date/View runtime and parsed-card cache contracts. External ID, URL, final-page category/location and remote exact-view identity are revalidated at trust boundaries so stale/corrupt cross-service payloads fail closed.
