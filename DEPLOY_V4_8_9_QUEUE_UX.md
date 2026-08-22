# DT PARSER v4.8.9 — Queue UX

UI-only queue release on top of v4.8.8.

## Behaviour
- Up to `MULTIUSER_LOCAL_WORKERS` scans run simultaneously (default 4).
- Extra launches remain queued FIFO.
- User card shows live position, occupied lanes, people ahead, and wait time.
- Position is updated by the existing progress ticker.
- Queue cancellation does not start network work.
- When a lane opens, the same card immediately switches to scan progress.
- If queue wait was >= `QUEUE_START_NOTIFY_AFTER_SECONDS` (default 8s), a one-time start notification is sent.

No parser, Date Worker, Page Worker, View Worker, traffic or Redis runtime logic was changed.
