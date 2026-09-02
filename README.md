# DT Parser 4.21.13 — Radar 3.2 Two-Pass Clean

## Radar scope
- AutoScan: 20 pages, today only.
- Automatic Radar excludes Auto, Immobilien, Jobs, Dienstleistungen, Unterricht & Kurse and Nachbarschaftshilfe.
- The same exclusion policy is applied to user-scan Radar baselines, so those sections cannot leak back into Radar through manual scans.
- The normal user parser is unchanged; exclusions apply only to Radar analytics.

## Category-adaptive demand
- First exact view counter is baseline only and contributes 0 points.
- <3 views/hour is hard noise.
- Candidate = P90 of the current leaf category.
- Early / Score = P95.
- Strong = P98.
- Hot interval = P99, with persistence or an independent family confirmation still required for Hot.
- First scored checkpoint remains capped at 50/100.

## 4.21.13 audit fixes
- Radar evaluation is two-pass: all refreshed velocities are persisted first, then one shared category cohort is built and applied to every row in that batch.
- New one-time reset marker clears RadarProduct, RadarObservation, RadarSnapshot, RadarFavorite, product links and lifecycle watches. Raw Listing/ViewHistory is preserved for audit.
- AutoScan policy version is bumped; old progress/history/counters are discarded while the user's daily schedule preference is preserved.
- Old in-progress rounds cannot resume with excluded categories.
- Canonical category scope is shared by AutoScan and user-scan seeding to prevent policy drift.

## Validation
Run `python -m compileall -q .`, `pytest -q`, and `python scripts/release_smoke.py`.
