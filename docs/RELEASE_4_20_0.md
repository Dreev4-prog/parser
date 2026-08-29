# 4.20.0 — DT Radar Core 2.0

## Consolidated baseline

This is the clean successor to the late 4.15.x line and retains:

- Organic Demand Integrity + sticky dirty registry;
- strict DB-authoritative/live Detail Organic Gate;
- exact-view recovery and external-id identity validation;
- Page/Date recovery hardening;
- purple Hochschieben/resurrection detection;
- `>=400` Verified Organic Velocity policy;
- AutoScan deadlock isolation, hard stop, interruptible cooldown and category watchdog;
- audited `v4200-core2-audit3` Page/Date/View runtime and parsed-card cache contract.

## Unified public 48H Radar

The Fresh and Context scanners keep separate jobs, but their **qualified evidence feeds one Radar**.

### Fresh Layer

- target: today;
- depth: 15 pages/category;
- cadence: every normal AutoScan;
- exact views + Organic Gate required before Radar admission.

### Context Layer

- target: yesterday;
- depth: 15 pages/category;
- cadence: at most once per Moscow calendar day;
- starts after a completed manual or daily Fresh round;
- does not require the daily toggle;
- uses the same exact-view and Organic Gate pipeline;
- may publish a yesterday listing into the same public Radar **only if its views are demand-safe and it qualifies under the same 48H ranking rules**.

An inherited yesterday total is not automatically trusted. If its provenance is baseline/unknown, it cannot score. First-seen `>=400` remains withheld until two later clean checkpoints; then only the observed delta can vote.

### Age clock vs verified-delta clock

Unified 48H deliberately keeps two clocks separate:

- **listing age** decides the `0–3 / 3–6 / 6–12 / 12–24 / 24–48h` cohort and the absolute Demand Gate;
- **DT observation window** is used only to convert a verified post-baseline delta into views/hour.

Example: an ad that is 29h old but was first baselined by DT 1h ago remains in the **24–48h cohort** and must clear the **100-view** Hot gate. If it gained `+120` verified organic views during that 1h observation window, Relative Velocity evaluates the real `120 views/hour`; the inherited baseline still contributes zero. The baseline clock can never rejuvenate an old listing into the 0–3h cohort.

## Explicit age cohorts

Relative View Velocity is compared inside non-overlapping cohorts:

`0–3h / 3–6h / 6–12h / 12–24h / 24–48h`

A sparse cohort stays sparse; the model does not borrow a different age band merely to manufacture a Relative Velocity percentile.

## Absolute Demand Gate

DT Score alone is no longer sufficient to label a product Hot. Demand-safe views must also reach an age-aware floor:

| Evidence age | Full Hot gate |
| --- | ---: |
| 0–3h | 30 |
| 3–6h | 40 |
| 6–12h | 60 |
| 12–24h | 80 |
| 24–48h | 100 |

Classification:

- **Hot:** DT Score `>=72`, confidence `>=45`, full gate reached;
- **Strong:** DT Score `>=65`, confidence `>=35`, at least 60% of the full gate;
- **Early:** DT Score `>=58`, at least 25% of the full gate;
- otherwise the signal is historical/not admitted to live ranking.

Example: a 2-hour listing with 15 demand-safe views has only `15/30`; even with DT Score 95 it can be Early, never Hot.

## Radar Rank vs DT Score

DT Demand Score remains exactly:

`40% Relative View Velocity / 20% Acceleration / 15% Persistence / 15% Repeatability / 10% Price Fit`

No post-model repeat/confirmation bonuses are added to the public score.

A separate internal ordering metric is:

`Radar Rank = 70% DT Score + 20% Evidence Confidence + 10% Evidence Maturity`

Maturity only orders qualified evidence. It never changes DT Score.

Frozen evidence is conservatively aged: if no new exact views arrive, the stored demand count stays fixed while the age gate becomes stricter. An early Hot signal can therefore fall to Strong/Early instead of remaining Hot for 48 hours on an old counter.

## One-time synthetic-score cleanup

Pre-unified `radar_autoscan` and `scan_hot` snapshots used TOP position to manufacture a score. On first Parser startup this build:

1. deletes those synthetic snapshots;
2. removes active Lifecycle watches created from them;
3. clears live `current_score`, confidence, Radar Rank, Demand Gate and status on affected/current Radar products;
4. recomputes historical Peak Score only from surviving non-synthetic snapshots;
5. preserves stable product IDs, favorites and catalogue associations;
6. lets Fresh/Context/AI rebuild live status only from new demand-safe evidence.

## AI Picks and Lifecycle

- AI may still produce its evidence-adaptive DT Score, but public AI Picks require unified live status `Hot` or `Strong`.
- Lifecycle/Fast Sold enrollment requires a unified `Hot`/`Strong` signal as well as the existing score floor.
- A low-volume Early signal cannot start Fast Sold tracking merely because its relative score is high.

## Automatic DB changes

`init_db()` adds, without manual SQL:

- `radar_products.radar_rank`
- `radar_products.demand_views`
- `radar_products.demand_age_minutes`
- `radar_products.demand_gate`
- corresponding snapshot fields plus `radar_snapshots.demand_status`.

## Repository cleanup

Historical release notes/checksums remain consolidated under `docs/`; behavior-critical runtime files stay at repository root for Railway compatibility.

### Unified AutoScan circle UI

AutoScan is now presented to the administrator exactly as the product contract works: one **Unified 48H circle** with two stages — **15 pages today + 15 pages yesterday**. The live panel shows the current stage and a combined 2-stage progress counter. Completing Fresh no longer says that the whole circle is finished; only completion of the yesterday stage closes the Unified 48H circle. The underlying two-stage execution remains isolated internally for runtime stability.
