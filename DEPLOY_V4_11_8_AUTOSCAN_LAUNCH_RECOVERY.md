# DT PARSER v4.11.8 — AutoScan Launch Recovery

Base: **v4.11.7 Free Funnel Analytics**.

## Why this release exists

A manual Radar AutoScan could be persisted as `status=running` while actual execution still depended on the background AutoScan scheduler noticing the wake-up. If that scheduler was delayed/stuck between iterations, the admin could press **Запустить 1 круг** and see no category begin.

The parser/date/page/view algorithms themselves were not changed by v4.11.7. v4.11.8 hardens only the orchestration of starting/resuming an AutoScan round.

## Changes

- manual **Start** immediately kicks the AutoScan runner; it no longer depends only on scheduler wake-up
- **Resume** and **Retry failed** use the same immediate kick
- a dedicated single-flight lock guarantees scheduler + manual kick can never run two AutoScan parser rounds at once
- 20-second launch watchdog re-kicks a round if it is still `running` but untouched and not waiting for user scans
- a fresh round immediately displays `Запуск первой категории…` instead of an empty current-category line
- admin callback explicitly says either:
  - `Круг запущен · первая категория стартует сейчас`, or
  - `Круг запущен · ждёт пользовательские сканы: N`
- new launch logs:
  - `DT Radar AutoScan immediate kick reason=manual-start`
  - `DT Radar AutoScan runner entered ...`
  - `DT Radar AutoScan category start ...`
  - watchdog re-kick only if launch did not advance

## Unchanged

- 84 product-oriented AutoScan categories
- one warm parser session per circle
- product-only policy / cooldown / partial recovery
- exact Radar view collection
- DT Score / Radar feeds / free Radar preview / free funnel analytics
- Date/Page/View/AI worker code
- four foreground parser lanes

## Railway

No new variables and no DB migration.

Redeploy **parser service only**.

## Smoke test

1. Open Admin -> Radar AutoScan.
2. Press **Запустить 1 круг**.
3. With no user scans active, callback must say `первая категория стартует сейчас`.
4. Logs should immediately show `immediate kick`, then `runner entered`, then `category start`.
5. If a user scan is active/queued, AutoScan should visibly wait and start automatically when the foreground queue becomes empty.
6. Stop after category, then Resume; Resume should start immediately via the same runner path.
