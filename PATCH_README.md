# v4.23.27 — daily posts for all bot users

The user-facing promotion opt-out button is removed. Daily posts target all
registered bot users, including subscribers, administrators and people with old
promo_mute settings. Accounts banned by the administrator or unreachable through
Telegram are skipped. Delivery is independent of the subscription access mode.

Existing post audiences migrate automatically to "all" without resetting text,
photos, times or enabled state. Newly created posts also target everyone. Old
admin audience controls and old opt-out callbacks cannot restore filtering.

Includes the full autopost feature and both pictures; install over 4.23.25 or
4.23.26, preserving the assets/ directory. The 12:00 / 17:00 Moscow schedule
and separate 20:00 Daily Radar remain. See INSTALL_AUTOPOSTS_RU.txt.
