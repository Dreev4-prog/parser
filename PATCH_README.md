# v4.23.9 GitHub patch — Vinted Radar Demand Quality

Apply **on top of v4.23.8** and preserve all paths from this archive.

After push/redeploy:

- redeploy **Parser / Bot** — required;
- Vinted Scan Worker code is unchanged;
- Vinted Metrics / Session workers are unchanged;
- Kleinanzeigen Page / Date / View / Radar workers are unchanged.

No manual SQL migration and no new required Railway variables.

Main changes:

1. Vinted Radar baseline/Score ignores listings below **40 EUR**;
2. Like Momentum uses the actual item/page observation timestamp, not the full-round start time;
3. current peer percentiles use only current 24h Live items, not expired 7-day history;
4. Price Edge requires a stronger peer cohort and Deals need real demand evidence;
5. Radar UI shows the observation funnel: one sample -> repeated -> positive like movement.

Optional overrides exist for `VINTED_RADAR_MIN_PRICE_EUR` and `VINTED_RADAR_MIN_PRICE_PEERS`, but the production defaults are 40 EUR and 8 peers.

See `RELEASE_4_23_9.md` and `AUDIT_4_23_9_VINTED_DEMAND.md`.
