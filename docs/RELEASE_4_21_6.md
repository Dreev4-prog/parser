# DT Parser 4.21.6 — Radar 3.0 Demand Gate

## Changes
- Hard pre-score demand funnel based only on DT-observed velocity after baseline.
- <15 views/hour: Noise/Weak, no DT Score, observation stops.
- 15–29 views/hour: Candidate, no DT Score, observation continues.
- 30–59 views/hour: admitted to public Radar and DT Score is calculated.
- >=60 views/hour: Strong on the first qualifying interval.
- Thresholds use normalized views/hour so delayed checkpoints cannot inflate demand.
- One-time reset marker v2 deletes all previous Radar observations/products/snapshots/favorites/lifecycle watches while preserving raw Listing/ViewHistory.
- Dashboard now exposes Candidate and Score Gate counts.
