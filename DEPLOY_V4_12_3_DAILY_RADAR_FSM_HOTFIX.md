# DT PARSER 4.12.3 — Daily Radar FSM Hotfix

Base: **4.12.2 Daily Radar Instant UI**.

## Fixed
- `admin_daily_radar_handler()` used the argument name `fsm: FSMContext`.
- aiogram 3 FSM middleware exposes the context to handlers as `state`, so the callback was invoked without `fsm` and failed with:
  `TypeError: admin_daily_radar_handler() missing 1 required positional argument: 'fsm'`.
- The handler now accepts `state: FSMContext` and clears it normally before opening Daily Radar.
- Internal Daily Radar config in the same handler is renamed to `digest_state` so it cannot shadow the FSM context.

## Preserved
- v4.12.2 instant loading UI and bounded metric reads.
- Manual `📣 Отправить сейчас`.
- Custom `HH:MM` Moscow time.
- Daily scheduler and duplicate-send protection.
- v4.11.9 AutoScan view deadlock recovery.
- DT Radar / Free Radar / funnel analytics.
- Parser/Page/Date/View/AI core unchanged.

## Deploy
Redeploy **parser service only**. No DB migration and no new Railway variables.

## Smoke test
1. Start parser and confirm `version=4.12.3`.
2. Open Admin → `📨 Daily Radar`.
3. The callback must immediately show the loading screen and then the Daily Radar panel.
4. Check custom time and `📣 Отправить сейчас` confirmation.
