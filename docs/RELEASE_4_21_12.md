# DT Parser 4.21.12 — Radar 3.2 Two-Pass Clean

- New one-time Radar reset removes old observations/products/snapshots/favorites/lifecycle watches while preserving raw Listing/ViewHistory.
- Radar scope excludes Auto, Immobilien, Jobs, Dienstleistungen, Unterricht & Kurse and Nachbarschaftshilfe from both AutoScan and user-scan baselines.
- AutoScan policy v6 discards old progress/history/counters while preserving daily schedule preferences.
- Category-adaptive evaluation is two-pass: measurements first, shared per-category P90/P95/P98/P99 evaluation second.
- Dashboard funnel counts evaluated statuses, not percentile alone, preventing small-cohort false Candidate/Strong counts.
- AutoScan remains 20 pages, today only.
