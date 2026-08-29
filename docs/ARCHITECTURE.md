# DT Radar Core 2.0 — Architecture

## 1. Foreground priority

User scans always have priority. AutoScan pauses before starting a new category when foreground work is running/queued. During an AutoScan round, parser-side background integrity/checkpoint traffic is paused so it cannot invert locks with the foreground Detail Gate.

A category has a bounded watchdog. Hard Stop cancels the in-flight AutoScan category, resets its browser context and leaves the category index unchanged for a clean resume.

## 2. Fresh Layer

Every normal/manual/daily Fresh round scans up to 15 verified target-date pages per eligible product category for **today**.

Pipeline:

`Date/Page chronology -> card integrity -> exact views -> view provenance -> live Organic Gate -> Radar admission`

A category with incomplete exact views or unresolved higher-ranked Organic Gate evidence is fail-closed and goes to retry instead of fabricating a TOP.

## 3. 48H Context Layer

At most once per Moscow calendar day, after a completed manual or daily Fresh Layer, DT launches a separate Context round for **yesterday**, also capped at 15 verified pages/category. This does not require the daily toggle to be enabled.

Context uses the same Date/Page/exact-view/Organic Integrity machinery. Its strongest demand-safe rows pass the live detail gate so hidden paid visibility can be stickily removed. However `emit_signals=False` means the Context round does not publish yesterday's inherited totals as new public Radar snapshots.

The useful output is durable market evidence in Listings/ViewHistory/PriceHistory. That evidence improves Persistence, Repeatability, price profiles and historical growth comparisons for subsequent demand scoring.

## 4. View provenance

First exact DT observation:

- `0..399`: may be trusted only when the listing is genuinely fresh/clean; ambiguous older history remains baseline-only.
- `>=400`: always untrusted baseline, regardless of apparent publication day.

For a high baseline, two later clean exact measurements at least 30 minutes apart are required. Then:

`demand_views = current_exact_views - baseline_views`

The inherited total never votes in DT Demand Score.

## 5. Age cohorts

Relative velocity uses comparable-age cohorts:

- 0–3 hours
- 3–6 hours
- 6–12 hours
- 12–24 hours
- 24–48 hours

Sparse cohorts may fall back to broader category evidence, but the preferred comparison is always age-matched.

## 6. Organic Integrity

Sticky exclusions remain for:

- TOP / Top-Anzeige
- Hochschieben, including the purple bump icon
- paid Highlight / Galerie / sponsored markers
- reduced/crossed-out price evidence
- same external ID resurfacing with impossible chronology

A positive dirty verdict purges that listing's Radar/AI/Lifecycle evidence. Unknown is not treated as organic.

## 7. DT Demand Score

The fixed model is:

- 40% Relative View Velocity
- 20% Acceleration
- 15% Persistence
- 15% Repeatability
- 10% Price Fit

The model is evidence-adaptive: missing history does not behave like a fake 50% observation.


## 8. Parser/cache integrity audit

Card promotion parsing is schema-versioned. v4.20.0 audited builds use `v4200-core2-audit3` in both Redis Page Worker cache and durable stable-page payloads, so older parsed cards cannot bypass corrected promotion semantics. Background browser fallback is paused together with background view/detail work while an AutoScan foreground round owns priority.
