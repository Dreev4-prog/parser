# DT PARSER 4.13.0 — Simple Referral Promo

Base: **4.12.3**.

## User flow

1. While the promo is enabled, the main menu shows `🎁 Получить день бесплатно`.
2. The user receives a personal link: `https://t.me/<bot>?start=ref_<telegram_id>`.
3. A referral is counted only if that Telegram user has never existed in `bot_users` before the referred `/start`.
4. Every two promo-eligible referred users atomically add **+1 day** to the referrer's `access_until`.
5. The mechanic repeats without a cap: 2 -> +1 day, 4 -> +2 days, 6 -> +3 days.

## Promo on/off

Admin path: `Админ-панель -> 👥 Рефералы`.

- ON: new attributed users count toward the 2 -> +1 day reward.
- OFF: referral attribution is still stored for analytics, but new entries during the pause are marked non-eligible and never retroactively earn promo days.
- Existing unfinished eligible progress is preserved across pause/resume.

## Integrity

- `referral_invites.referred_user_id` is unique, so one Telegram user can be attributed only once.
- Self-referrals are rejected.
- A referrer must already exist in `bot_users`.
- The two referral rows and the access extension are committed in one DB transaction.
- Existing active access is extended; expired/no access starts from current UTC time.

## Database

A new `referral_invites` table is created automatically with `CREATE TABLE IF NOT EXISTS` before `metadata.create_all` to avoid Railway multi-service first-start races.

No manual migration. No new Railway variables.

## Smoke test

1. Deploy parser and confirm startup logs contain `version=4.13.0`.
2. Open admin -> Referrals; confirm promo status and counters render.
3. Open a user's `🎁 Получить день бесплатно` screen and copy/share the deep link.
4. Start the bot from two Telegram accounts that have never used the bot before.
5. Confirm the referrer screen reaches 2 users and then returns to 0/2 progress with `+1 day` earned.
6. Confirm `access_until` increased by one day.
7. Disable promo; enter from a third brand-new account; confirm overall link entries rise but promo progress does not.
