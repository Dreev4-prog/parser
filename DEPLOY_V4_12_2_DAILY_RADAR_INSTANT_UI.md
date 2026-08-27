# DT PARSER 4.12.2 — Daily Radar Instant UI

Base: **4.12.1**.

## Fixed

- Admin `📨 Daily Radar` acknowledges the Telegram callback immediately.
- The screen first shows `⏳ Загружаю живые цифры…` instead of appearing dead.
- Daily Radar state reads, live Radar aggregates and recipient counting have bounded timeouts.
- Last successful live metrics are cached in the existing `AppSetting` JSON and can be displayed as a fallback.
- If fresh metrics cannot be obtained, manual broadcast is **not** sent with stale/zero data.
- `Время`, `Ввести своё время`, `Отправить сейчас` and `Тест только мне` now all acknowledge first and surface an explicit error instead of hanging silently.
- Added diagnostic log entry: `Daily Radar admin panel open requested admin=...`.

## Preserved

- Arbitrary `HH:MM` Moscow time selection from 4.12.1.
- Manual `📣 Отправить сейчас` flow with confirmation.
- One automatic send per Moscow day after a manual send.
- 4.11.9 AutoScan View Deadlock Recovery.
- Free Radar Preview, funnel analytics, DT Radar, AutoScan 84-category policy, Date/Page/View/AI workers.

## Deploy

No database migration. No new Railway variables. Redeploy **parser service only**.

After deploy, open `Админ-панель → 📨 Daily Radar`. The loading screen should appear immediately. If live metrics fail, the panel will show a warning and Railway logs will contain `Daily Radar metrics failed/timeout`.
