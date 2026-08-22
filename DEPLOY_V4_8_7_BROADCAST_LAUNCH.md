# DT PARSER v4.8.7 — Broadcast Launch

Based on the known-good v4.8.6 Coverage Complete parser core.

## Added

- Admin panel button `📣 Рассылка`.
- One unified composer: send text, photo, or photo + caption to the bot.
- Exact Telegram preview before delivery.
- Explicit confirmation; nothing is broadcast immediately after upload.
- Uses `copy_message`, so formatting/captions/photo quality are preserved without a forwarded-message header.
- Sends to every registered non-banned `bot_users` record, including expired subscribers.
- Delivery report: sent / bot blocked or chat unavailable / other failures.
- Throttled delivery with Telegram RetryAfter handling.

## Parser core

No Date/Page/View/scan-integrity behavior changed from v4.8.6.
