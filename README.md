# DT Parser 4.20.0 — DT Radar Core 2.0

Production Telegram/Railway parser and DT Radar for Kleinanzeigen.

## What this release is

4.20.0 consolidates the late 4.15.x integrity/recovery work into one clean baseline and adds the 48-hour market context model.

- Fresh Layer: 15 pages for **today** on every normal AutoScan.
- Context Layer: 15 pages for **yesterday**, at most once per Moscow day; it is queued after a completed manual or daily Fresh round.
- Context rows enrich market history but do **not** publish inherited yesterday totals directly into Radar.
- Age cohorts: `0–3h`, `3–6h`, `6–12h`, `12–24h`, `24–48h`.
- Initial exact counter `>=400` remains untrusted baseline; two later clean checkpoints are required and only the observed delta may score.
- Sticky Organic Integrity remains active for TOP/Hochschieben/Highlight/Galerie/sponsored/reduced-price/resurfaced IDs.
- v4.15.8 hard-stop, background deadlock isolation and per-category watchdog are preserved.

DT Demand Score remains:

`40% Relative View Velocity + 20% Acceleration + 15% Persistence + 15% Repeatability + 10% Price Fit`

Unknown evidence remains evidence-adaptive: unavailable factors do not inject synthetic neutral votes.

## Repository layout

Runtime entrypoints intentionally stay in the repository root because Railway services import them directly. Release/history clutter does not.

- `bot.py`, `parser.py`, `radar.py`, workers/managers — production runtime.
- `assets/` — Telegram/UI assets.
- `docs/DEPLOYMENT.md` — deployment checklist.
- `docs/ARCHITECTURE.md` — runtime/data-flow overview.
- `docs/RELEASE_4_20_0.md` — exact release behavior.
- `docs/releases/HISTORY.md` — all historical deployment notes consolidated into one archive document.
- `docs/checksums/HISTORY_SHA256.txt` — all historical release hashes consolidated into one archive file.
- `tests/` — release invariants/smoke tests.
- `scripts/release_smoke.py` — dependency-free local release validation.

## Deployment

See `docs/DEPLOYMENT.md`.

## Release validation

```bash
python scripts/release_smoke.py
python -m unittest discover -s tests -p 'test_*.py'
```

The smoke suite is dependency-free. A full live smoke test still requires Railway/PostgreSQL/Redis and the real worker fleet.


## Whole-project audit

This package is the final audited 4.20.0 build. See `docs/AUDIT_4_20_0.md`. Its Redis/cache contract marker is `v4200-core2-audit3`, intentionally isolated from the earlier pre-audit 4.20.0 assembly.
