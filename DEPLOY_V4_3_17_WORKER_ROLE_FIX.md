# DT Parser v4.3.17 — View Worker role fix

Fixes Railway auto-launch when the dedicated View Worker keeps an internal/generated service name.

- Main bot: BOT_TOKEN + DATABASE_URL -> `bot.py`
- Dedicated View Worker: REDIS_URL only -> `view_counter_worker.py`
- `parser.py` and `traffic.py` are unchanged from the v4.3.8-based stable core.

No DATABASE_URL is required in the View Worker service.
