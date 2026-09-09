# DT Parser v4.23.15 — Reliability Baseline

Base: v4.23.14 Radar Checkpoint Runtime Fix.

## What changed

- A clean checkout now includes a safe `.env.example`; real secrets remain ignored.
- Python bytecode, test caches, local databases, logs and generated exports are no
  longer tracked or eligible for accidental commits.
- Historical deploy notes and legacy checksum lists moved from the repository root
  into `docs/archive/` without deleting their Git history.
- Contradictory historical tests were retired or updated to current contracts:
  two-category scans, exact inline baselines, 20-page today-only Radar, the inert
  legacy AI service, and `service_launcher.py` as Railway's root entrypoint.
- The full suite can collect from a clean checkout. A missing test fake model was
  restored and the shared-browser runtime health probe now has a real implementation
  plus direct regression tests.
- GitHub Actions now runs compilation, the runtime-global audit, release smoke checks
  and the complete pytest suite on pushes and pull requests.
- Current architecture and Railway deployment documentation replace the obsolete
  4.20 Unified 48H / active AI Worker instructions.

## Runtime impact

Radar scoring, Candidate/Early/Strong/Hot thresholds, exact-view behavior, scan
depth, database schema, PostgreSQL data and Redis state are unchanged. The browser
health helper is defensive only: it reports a stopped runtime if the browser object
is missing, disconnected or cannot be inspected.

## Deployment

Redeploy Parser / Bot from the complete v4.23.15 checkout. No SQL migration, data
cleanup, Radar reset or new Railway variable is required. Other services may remain
on v4.23.14 because their runtime algorithms did not change, although keeping the
fleet on one commit is recommended.

## Verification

Run:

```bash
python -m compileall -q .
python scripts/check_runtime_globals.py
python scripts/release_smoke.py
python -m pytest -q
```
