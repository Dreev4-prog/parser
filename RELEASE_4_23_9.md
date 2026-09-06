# DT Parser v4.23.9 — Vinted Radar Demand Quality

## Why

Production Vinted Radar 1.0 showed a very large baseline dominated by low-price items while HOT/RISING remained tiny and Deals were noisy. The issue was not just the status thresholds. The scoring path had three quality defects:

1. no minimum price gate for Radar baseline/scoring;
2. catalog observation time used the full round start time instead of the actual page/item persist time;
3. current peer percentiles could include expired 7-day learning items.

That combination can make price and Like Momentum statistics look much stranger than the real market.

## 1. Radar price floor: 40 EUR

Vinted Radar now ignores listings below **40 EUR** for baseline, peer statistics and Score.

Default:

`VINTED_RADAR_MIN_PRICE_EUR=40`

The environment variable is optional. Manual Vinted Parser results are unchanged.

The SQL snapshot query also applies the floor before loading rows, so low-price history no longer consumes the expensive seven-day scoring pass. Existing low-price rows may remain stored until normal retention, but they immediately stop participating in Radar.

## 2. Correct measurement time

Every catalog observation now uses `VintedScanItem.created_at` — the time the page/item was actually persisted.

Previously every item in a full-market round inherited `VintedScan.created_at`. On a long multi-segment pass, an item measured near the end of the round could therefore look as if it had been measured hours earlier. That directly distorted:

- Like Velocity;
- interval length;
- acceleration;
- first-seen age buckets.

v4.23.9 fixes the time axis without changing raw likes.

## 3. Live-only peer normalization

Current P50/P90-like peer comparisons now use only items that are still in the current 24-hour Live set.

Expired items remain in the 7-day learning pool but no longer vote in today's:

- likes percentile;
- velocity percentile;
- brand momentum reference.

Age buckets now use current DT-observed age, not only the duration between first and last successful sample.

## 4. Cleaner Deal signal

A low price alone is no longer enough to become a Deal.

Price Edge now requires a useful peer cohort (default **8** observations). Unknown `catalog_id` rows are never merged into one fake mega-market for price statistics.

A Deal also needs visible demand evidence:

- confirmed positive like movement; or
- at least 2 current likes and at least median likes versus its live peer cohort.

HOT/RISING still require confirmed positive like movement. Their thresholds are not loosened just to manufacture more signals.

## 5. Observation funnel in Vinted Lab

The Radar screen now separates the real sampling problem from the score:

- one observation;
- seen again in a later snapshot;
- confirmed positive like movement;
- HOT / Rising / Deals / Candidates;
- no confirmed signal.

It also shows the active **>=40 EUR** Radar floor.

This makes it possible to tell whether low HOT/RISING counts come from scoring or simply from too few repeated observations. If repeat coverage remains low after this release, the next optimization should be a dedicated Vinted follow-up/recheck lane rather than weaker thresholds.

## Compatibility

- Apply on top of v4.23.8.
- Parser / Bot redeploy required.
- Vinted Scan Worker code is unchanged.
- Vinted Metrics / Session workers are unchanged.
- Kleinanzeigen Parser / Radar 3.2 is unchanged.
- No manual SQL migration.
- No new required Railway variables.
