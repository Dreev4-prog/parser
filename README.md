# DT Parser 4.21.4 — DT Radar 3.0 Observed Demand

Production Telegram/Railway parser and DT Radar for Kleinanzeigen.


## Radar 3.0: first counter is never demand

Kleinanzeigen can surface an old listing as if it were fresh while keeping its historical view counter. Radar 3.0 therefore treats the first exact counter only as a baseline. Public signals are created solely from growth DT measures afterwards. See `docs/RADAR_3_0.md`.

## Unified 48H Radar

4.20.0 uses one public Radar for **today + yesterday** instead of treating yesterday only as background statistics.

- **Fresh Layer:** up to 15 verified pages/category for today on every normal AutoScan.
- **Context Layer:** up to 15 verified pages/category for yesterday, at most once per Moscow day after a completed manual/daily Fresh round.
- Both layers use the same Date/Page chronology, exact views, view-provenance rules and live Organic Gate.
- A yesterday listing may enter the same Radar, but only from **demand-safe evidence**. An inherited/unknown total never becomes a shortcut into TOP.
- Relative View Velocity is compared only inside explicit age cohorts: `0–3h`, `3–6h`, `6–12h`, `12–24h`, `24–48h`.

### Demand Gate

DT Score is relative, so Radar adds an absolute demand floor before a listing can be called Hot:

- `0–3h`: 30 demand-safe views
- `3–6h`: 40
- `6–12h`: 60
- `12–24h`: 80
- `24–48h`: 100

Statuses:

- `🟡 Early` — interesting early evidence, but not enough proof for Strong/Hot;
- `📈 Strong` — demand is already confirming;
- `🔥 Hot` — DT Score + confidence + full age-aware Demand Gate are all satisfied.

A listing with 15 views can therefore never become Hot simply because a weak category makes its relative percentile look good.

### Radar Rank

Public **DT Demand Score stays unchanged**. Ordering uses a separate internal rank:

`Radar Rank = 70% DT Score + 20% Evidence Confidence + 10% Evidence Maturity`

Radar Rank does not rewrite DT Score. Stale demand is re-evaluated against the older age gate even without inventing new views, so a listing cannot stay Hot forever on an early frozen counter.

## DT Demand Score

The public score remains:

`40% Relative View Velocity + 20% Acceleration + 15% Persistence + 15% Repeatability + 10% Price Fit`

Unknown factors remain evidence-adaptive: unavailable evidence is removed from the vote instead of injecting a synthetic neutral score.

## View provenance / Organic Integrity

- First exact counter `>=400` is always an untrusted baseline.
- Two later clean checkpoints, at least 30 minutes apart, are required.
- Only `current - baseline` may then contribute to demand scoring.
- Sticky exclusions remain for TOP/Hochschieben/Highlight/Galerie/sponsored/reduced-price/resurfaced external IDs.
- High views alone never mark an ad promoted.

## Reliability retained

The audited 4.20.0 baseline preserves hard stop, category watchdog, foreground/background deadlock isolation, exact-view identity checks, Page/Date payload trust boundaries and the `v4200-core2-audit3` rolling-deploy/cache contract.

On first startup, old `radar_autoscan` / `scan_hot` snapshots that used the previous synthetic TOP-position score are removed from live ranking. Product ids/favorites/history remain, while current status is rebuilt only from new unified demand-safe evidence.

## Repository layout

Runtime entrypoints intentionally remain in the repository root because Railway services import them directly. Release clutter does not.

- `bot.py`, `parser.py`, `radar.py`, workers/managers — production runtime.
- `radar_ranking.py` — unified 48H Demand Gate / Radar Rank policy.
- `assets/` — Telegram/UI assets.
- `docs/DEPLOYMENT.md` — deployment checklist.
- `docs/ARCHITECTURE.md` — runtime/data-flow overview.
- `docs/RELEASE_4_20_0.md` — exact release behavior.
- `docs/AUDIT_4_20_0.md` — whole-project audit notes.
- `docs/releases/HISTORY.md` — historical deployment notes consolidated into one archive document.
- `docs/checksums/HISTORY_SHA256.txt` — historical release hashes consolidated into one archive file.
- `tests/` — release invariants/smoke tests.
- `scripts/release_smoke.py` — dependency-free local release validation.

## Validation

```bash
python scripts/release_smoke.py
pytest -q
```

A full live smoke test still requires Railway/PostgreSQL/Redis and the real Kleinanzeigen worker fleet.
