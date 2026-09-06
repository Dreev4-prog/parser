# v4.23.10 GitHub patch — Vinted Radar Follow-up Lane

Apply **on top of v4.23.9** and preserve all paths from this archive.

After push/redeploy:

- redeploy **Parser / Bot** — required (scheduler, discovery, Radar scoring/UI);
- redeploy **all Vinted Metrics Worker replicas** — required (targeted follow-up execution);
- Vinted Scan Worker algorithm is unchanged, but same-commit redeploy is recommended;
- Vinted Session Worker is unchanged;
- Kleinanzeigen Page / Date / View / Radar workers are unchanged.

`init_db()` creates the additive `vinted_radar_watches` table automatically. No manual SQL migration is required.

Production defaults:

- full-market discovery: every **60 min**;
- follow-up checkpoints: **+30 / +60 / +120 / +180 min**;
- discovery lookback: **90 min**;
- minimum discovery score: **42/100** (watch-selection only, not user-facing Vinted Score);
- maximum new watches/hour: **1500**;
- maximum active watches: **4500**;
- existing watches continue to finish when AutoScan is stopped; no new watches are seeded while AutoScan is off.

Optional tuning variables are documented in `RELEASE_4_23_10.md`; no new required Railway variable exists.
