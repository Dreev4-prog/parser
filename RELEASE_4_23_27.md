# DT Parser 4.23.27 — promotion posts delivered to all users

The campaign owner requested removal of the opt-out button and delivery to all
registered bot users. New posts and previews no longer include promotion mute
controls. Subscribers and administrators are included, and old `promo_mute:*`
settings no longer affect recipient selection. Only administrator-banned users
and Telegram-unreachable recipients are skipped.

A one-time settings migration changes saved post audiences to `all` while
preserving names, photos, captions, daily times and enabled/deleted state. New
posts use the same all-users policy. Frozen payloads of partially sent 4.23.26
runs use that policy too; the durable recipient cursor still prevents replaying
already processed users. Old admin keyboards cannot restore audience filtering.

The post scheduler works independently of the global subscription access mode.
The existing admin editing/preview/pause controls, 12:00 and 17:00 Moscow daily
slots, separate 20:00 Daily Radar and bounded run-history retention are retained.
No manual database commands, new dependencies or Railway variables are needed.
