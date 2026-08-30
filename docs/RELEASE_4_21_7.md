# DT Parser 4.21.7 — Radar 3.1 Context Score

- Keeps the 15/30/60 absolute Demand Gate.
- Adds category-relative velocity percentile (50 score points).
- Adds scored persistence (25 points), acceleration (15), and family repeatability (10).
- Separates Confidence from DT Score.
- Caps the first scored checkpoint at 50/100.
- Adds adaptive rechecks: Candidate 60m, Early 45m, Strong 30m.
- Adds two Hot paths: two consecutive >=60/h intervals, or Strong/persistent + second scored family listing.
- Adds Radar dashboard funnel, acceleration/confidence counts, and per-category context.
- Adds additive PostgreSQL columns for Radar 3.1 evidence state.
- Uses a new one-time Radar reset marker so old 4.21.6 scores do not mix with Radar 3.1 semantics.
