# DT PARSER v4.12.0 — Daily Radar Growth Loop

Base: **v4.11.9 AutoScan View Deadlock Recovery**.

## What changed

v4.12.0 adds a permanent daily DT Radar marketing digest. It uses only live persisted metrics and sends one message per Moscow calendar day to every registered, non-banned bot user.

Default schedule: **20:00 MSK**.

The digest includes, when available:
- AutoScan listings checked today
- new listings seen by AutoScan today
- categories processed today
- Radar signals recorded today
- new Radar products added today
- current Hot / Rising / AI Picks totals
- total persistent Radar product base
- best DT Score recorded today

Free users get a CTA to the existing five-item Radar preview and full-access button. Active subscribers get a shorter CTA to their already-unlocked Radar.

## Funnel tracking

The `📡 Открыть DT Radar` button uses a dedicated callback. Free-preview users who enter through the daily digest are recorded as `daily_digest_open` and also as a normal `radar_open`, so the existing Free Radar funnel keeps its full conversion chain.

Admin funnel now includes:

`📨 Пришли из Daily Radar: 24h / all-time`

## Admin controls

Open:

`Админ-панель -> 📨 Daily Radar`

Available controls:
- enable / disable Daily Radar
- choose 12:00 / 18:00 / 20:00 / 22:00 MSK
- send a test only to the current admin
- refresh live numbers

The admin page also shows the next run, last send, last delivered count, and today's live Radar counters.

## Restart safety

The setting and `last_sent_date` are persisted in the existing `app_settings` table. The send date is reserved before fan-out, so a Railway restart cannot cause the same daily campaign to be sent twice.

On the first deployment only: if v4.12.0 is installed after 20:00 MSK, the system does **not** immediately blast the audience. It begins the automatic cadence the next day. If installed before 20:00 MSK, the first automatic digest can go out at 20:00 the same day.

## Delivery behavior

Recipients: all registered `bot_users` where `is_banned = false`, including expired subscribers. Telegram `RetryAfter` is respected and sending is throttled to stay below common bot broadcast limits.

Daily commercial delivery is suspended automatically while the project access mode is `admin-only`.

## Deployment

No database migration and no new Railway variables.

Redeploy **parser service only**. Dedicated Date/Page/View/AI workers are unchanged.

## Smoke test

1. Confirm startup log contains `version=4.12.0`.
2. Open `Админ-панель -> 📨 Daily Radar`.
3. Confirm status is ON and default time is 20:00 MSK.
4. Press `🧪 Тест только мне` and verify the message contains live numbers and `📡 Открыть DT Radar`.
5. From a free account, press the daily Radar button and confirm the normal five-item preview opens.
6. Check `Бесплатные сканы -> Воронка бесплатного Radar`; `📨 Пришли из Daily Radar` should increment for a free-preview account.
