# DT PARSER v4.11.1 — DT Radar AutoScan Error Recovery

Base: v4.11.0 DT Radar AutoScan / v4.10.2 parser reliability core.

## What changed

- Stores each failed AutoScan category with key/name/reason/verified pages.
- Adds category coverage and requested-page coverage to completion reports/history.
- Adds paginated `Ошибки последнего круга` admin view.
- Adds `Повторить только ошибки`; only failed categories are rescanned.
- Retry rounds preserve the original target date and foreground-user priority.
- Retry completion recomputes logical coverage against the original round; full recovery reports 141/141.
- Remaining failures can be retried again without rescanning already recovered categories.
- Direct error/retry buttons are attached to completion notifications when failures remain.

## Important upgrade note

v4.11.0 stored aggregate `failed=N` but did not store failed category keys. Therefore a round that already finished before this upgrade cannot be targeted exactly from its old summary. Starting with the first v4.11.1 round, detailed errors are persisted and targeted retry is available.

## Railway

No new variables are required. Deploy the main `parser` service. Date/Page/View/AI algorithms are unchanged by this release.

## Smoke test

1. Open Admin -> Radar AutoScan.
2. Start one round.
3. If one or more categories finish partial, the final report must contain `Ошибки круга` and `Повторить только ошибки`.
4. Open errors and verify category/reason/page count.
5. Press retry. Progress total must equal only the number of failures, not 141.
6. If all retry categories succeed, final coverage must report the original total fully covered (e.g. 141/141).
7. Verify normal user scans still pause the start of the next AutoScan category.
