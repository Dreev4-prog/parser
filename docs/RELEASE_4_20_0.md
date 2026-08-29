# 4.20.0 — DT Radar Core 2.0

## Consolidated baseline

This release is the clean successor to 4.15.8 and retains:

- Organic Demand Integrity and sticky dirty registry;
- strict DB-authoritative Organic Radar Gate;
- complete exact-view recovery;
- Date/Page recovery hardening;
- purple Hochschieben/resurrection detection;
- `>=400` Verified Organic Velocity baseline policy;
- AutoScan deadlock isolation, hard stop, interruptible cooldown and category watchdog.

## New: 48H Market Context

Fresh and historical responsibilities are now separate.

### Fresh Layer

- target: today;
- depth: 15 pages/category;
- cadence: every normal AutoScan;
- may emit public Radar signals after exact views and strict Organic Gate.

### Context Layer

- target: yesterday;
- depth: 15 pages/category;
- cadence: at most once per Moscow calendar day;
- starts automatically after a completed manual or daily Fresh Layer;
- does not require the daily toggle to be enabled;
- records exact counters/history and performs live Organic checks on strongest demand-safe rows;
- **does not emit inherited yesterday totals as public Radar signals**.

This means 15 today + 15 yesterday gives DT a real 48-hour evidence window without turning yesterday's older totals into artificial winners.

## New: explicit age bands

Demand scoring now prefers:

`0–3h / 3–6h / 6–12h / 12–24h / 24–48h`

AI's public initial-candidate freshness default remains 24 hours, so yesterday's Context layer cannot become a second public feed. Verified Organic Velocity and historical demand evidence can still evaluate certified observations across the 24–48h cohort.

## Score policy unchanged

`40 / 20 / 15 / 15 / 10` remains unchanged.

`>=400` initial exact views policy remains unchanged: baseline contributes zero until two clean follow-ups; only observed delta may vote.

## Repository cleanup

Historical release notes and hashes were removed from the root and consolidated into `docs/releases/HISTORY.md` and `docs/checksums/HISTORY_SHA256.txt`. Runtime files remain at root intentionally to avoid breaking Railway entrypoints/imports.


## Full-file audit hardening

The consolidated archive received an additional whole-project audit before handoff. The audit fixed:

- duplicate `_human_duration` definitions in `bot.py` that silently changed AutoScan duration formatting;
- an icon detector false positive where normal product URLs/titles containing words such as `push-up`, `hochschiebe` or `boost` could be mistaken for Hochschieben;
- background local browser fallback not obeying the AutoScan background pause;
- Fresh/Context page-depth reporting using the Fresh constant in a few Context/retry paths;
- manual Fresh rounds not always queuing the once-daily yesterday Context when the daily toggle was disabled;
- stale Page Worker/stable-page cache reuse after the promotion-parser semantic fix.

The corrected card cache schema is `v4200-core2-audit3`.

## Final audited runtime contracts

The final packaged 4.20.0 baseline additionally hardens cross-service contracts:

- Page/Date/stable parsed-card schema: `v4200-core2-audit3`;
- Page/Date/View Redis runtime namespace marker: `v4200-core2-audit3`;
- exact view responses and browser redirects are bound to the requested `external_id`;
- Lifecycle availability also requires exact listing identity;
- PostgreSQL additive startup migrations are serialized with an advisory transaction lock;
- sparse 48H age cohorts stay sparse instead of falling back to all-age category velocity.

See `docs/AUDIT_4_20_0.md` for the whole-project audit matrix.
