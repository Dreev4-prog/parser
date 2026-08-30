# DT Parser 4.21.7 — Radar 3.1 Context Score

Production Telegram/Railway parser and DT Radar for Kleinanzeigen.

## Current Radar contract

Radar scans **20 pages for today only** per eligible product category. Completed user scans for today may also seed exact-view baselines. The first Kleinanzeigen counter is always baseline-only and contributes zero demand evidence.

### Absolute Demand Gate

- `<15 views/hour` — Noise/Weak, no Score, observation stops.
- `15–29 views/hour` — Candidate, no Score, recheck after 60 minutes.
- `30–59 views/hour` — Score/Early evidence, recheck after 45 minutes.
- `>=60 views/hour` — Strong first signal, recheck after 30 minutes.

All rates are calculated only from post-baseline growth observed by DT.

### DT Score after the 30/h gate

`50% category velocity percentile + 25% persistence + 15% acceleration + 10% repeatability`

The first scored checkpoint is capped at 50/100 because persistence and acceleration have not yet been proven. Confidence is a separate 0–95% evidence-maturity metric.

### Hot

A product can become Hot by either route:

1. one listing sustains `>=60/h` for two consecutive DT checkpoints; or
2. a Strong/persistent listing is confirmed by a second independent listing in the same product family that also crosses the `>=30/h` Score Gate.

### Radar dashboard

The single DT Radar admin panel shows the observation funnel, due queue, any growth, Candidate/Score/Strong counts, persistence, acceleration, high-confidence evidence, Early/Strong/Hot, total DT-observed growth, and category context (average velocity, best percentile, average confidence).

## Reliability

Radar observations have cross-replica PostgreSQL leases (`FOR UPDATE SKIP LOCKED`), a six-hour TTL, an independent expiry loop, and a dedicated throttled `radar_checkpoint` traffic lane. User scans keep foreground priority. Legacy AI admission is retired.
