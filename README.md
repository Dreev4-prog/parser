# DT Parser 4.21.12 — Radar 3.2 Frozen Cohort Full Audit Fix

Production release after full audit of 4.21.11.

## Radar 3.2
- AutoScan: 20 pages, today only.
- Auto/Immobilien/Jobs/Dienstleistungen/Unterricht/Nachbarschaftshilfe are excluded from every Radar ingestion path (AutoScan and user-scan baselines), while the normal parser still supports them.
- First exact counter is baseline-only and contributes 0 score.
- First DT checkpoint remains ~60 minutes.
- <3 views/hour is absolute noise.
- Mature categories use adaptive P90 Candidate / P95 Early+Score / P98 Strong / P99 Hot interval gates.
- Small cohorts (<20 measured intervals) use conservative bootstrap gates until enough category evidence exists.
- Category classification is two-pass with one frozen cohort for the whole refreshed batch; quiet/zero-growth rows remain in the distribution.
- First scored interval is capped at 50/100.
- Hot requires persistence or independent product-family confirmation.
- Products are retired immediately when no active Early/Strong evidence remains.

## Integrity
- Radar reset marker v6 clears old Radar observations/products once; raw Listing/ViewHistory is preserved.
- Cross-replica observation leases, TTL, stale cleanup, organic gates and Radar checkpoint traffic lane remain enabled.
- Legacy AI worker stays retired/inert.

See `docs/RELEASE_4_21_12.md` for audit details.
