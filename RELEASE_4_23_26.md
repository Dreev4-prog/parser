# DT Parser 4.23.26 — scheduled Telegram campaign posts

Two approved promotions, each a photo with an HTML caption and inline buttons,
are initialized once and enabled at 12:00 and 17:00 Europe/Moscow. The existing
20:00 Daily Radar remains independent and unchanged.

The admin Auto Posts menu supports new posts, names, captions, photo replacement
or removal, daily send time, preset action buttons, audience selection,
previews sent only to the administrator, per-post enable/disable, deletion and
global pause. New custom posts start disabled. Telegram caption formatting is
preserved and the 1024 UTF-16 caption limit is validated before saving.

Defaults target registered users without active subscriptions. Current
subscription status, bans and promotion opt-out are checked again before each
send. Admins are excluded. "All users" is an explicit per-post admin option.
The opt-out control affects only these promotions, not scan or payment messages.

Runtime safeguards:

- The main parser owns the scheduler; no new Railway service is needed.
- New due slots are recorded while earlier broadcasts are still in progress.
- A unique post/Moscow-date run and expiring database leases serialize senders.
- An ascending user cursor is persisted before each send. A restart or ambiguous
  network timeout can omit one in-flight message but cannot replay that user.
- Telegram RetryAfter is retried only after the requested delay.
- Due-slot creation catches up for 15 minutes. Fresh installs/enabling or moving
  a post never send an hours-old post immediately. A run can resume for 2 hours.
- One small row per post/day tracks outcomes; completed run history is kept for
  30 days. No per-recipient delivery table or image blobs grow in PostgreSQL.
- Bundled pictures are uploaded once per run and then reused via Telegram file_id.
- The one-time seed marker preserves admin edits/deletions across redeploys.

The existing database initializer automatically creates `promo_posts` and
`promo_runs`. No dependency, manual SQL or environment change is required.
The Radar seven-day query window and existing non-marketing behavior are retained.
