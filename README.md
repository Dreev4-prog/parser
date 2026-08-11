# Kleinanzeigen Parser Bot v2.6.0 — Multi-User Core

v2.6 keeps Smart Analytics + Fast Incremental from v2.5 and changes the scan engine so the bot can serve many users without starting the same Kleinanzeigen work over and over.

## What changed

### 📥 User job queue

`▶️ Начать парсинг` no longer runs the whole scan inside the Telegram callback.

Each user receives a job:

- one active/queued job per user;
- up to `MAX_CONCURRENT_JOBS` jobs are processed at once (default: 3);
- the rest wait in the queue;
- the user sees an approximate queue position and live progress;
- a queued/running job can be cancelled;
- `📥 Очередь` shows the current load.

The default logical queue limit is 200 jobs (`MAX_QUEUE_SIZE`).

### 🧠 Shared category cache

A category successfully scanned recently is reused by everyone.

Default:

```env
CATEGORY_CACHE_TTL_SECONDS=300
```

Example: user A scanned `Konsolen` 2 minutes ago. User B requests `Konsolen` now. No new category scan is started; B uses the common database immediately.

The cache is based on the global `category_scan_state`, so users share collected Kleinanzeigen data while keeping their own selected categories and output settings.

### 🤝 In-flight scan coalescing

If several users request the same stale category at the same moment, exactly one network scan is started for that category. The other jobs wait for that same task and reuse its result.

Example:

```text
User 1 → Konsolen ┐
User 2 → Konsolen ├→ ONE Kleinanzeigen scan → common DB
User 3 → Konsolen ┘
```

This is different from cache: cache reuses a recently completed scan; coalescing reuses a scan that is currently running.

### ⚡ Fast Incremental remains enabled

Once a category has had one full current-day seed, later real scans still use the v2.5 fast mode:

- start at page 1;
- collect the fresh prefix;
- stop after reaching the previous checkpoint / known tail;
- do not reread the whole current day every time.

So v2.6 has three possible sources for a requested category:

```text
🧠 cache     — zero Kleinanzeigen requests
🤝 shared    — another job is already scanning it
🌐 scan      — this job starts a real full/fast scan
```

### 🧱 SQLite concurrency hardening

Until PostgreSQL is added, SQLite now uses WAL, normal synchronous mode and a busy timeout. Listing upserts are serialized inside the bot process so parallel category scans do not race on the same ad ID.

This is suitable for testing the v2.6 architecture on one Railway service. It is **not** the final persistent multi-instance architecture: the in-memory queue disappears on a process restart and Railway's local SQLite may disappear after redeploy/restart.

## Telegram UI

Main menu includes:

- `▶️ Начать парсинг`
- `🗂 Категории`
- `⚙️ Настройки парсинга`
- `📦 Получить результат`
- `📥 Очередь`
- `📊 База`
- `📋 Что выбрано`

While a job is queued/running it has:

- `❌ Отменить мой запуск`
- `📥 Состояние очереди`

The final job summary shows how many requested categories were:

- really scanned over the network;
- served from cache;
- attached to an already-running shared scan;
- processed in Fast/Full mode;
- and how many network pages were actually used.

## Railway

Existing project/bot can be reused. Start command remains:

```bash
python bot.py
```

Required variables:

```env
BOT_TOKEN=...
ADMIN_IDS=123456789
```

`ADMIN_IDS` keeps the bot private. If it is empty, the existing access logic allows any Telegram user who opens the bot. For a public launch, add your own onboarding/subscription rules before sharing the bot widely.

Recommended v2.6 defaults for one Railway container:

```env
MAX_CONCURRENT_JOBS=3
MAX_QUEUE_SIZE=200
CATEGORY_CACHE_TTL_SECONDS=300
STATUS_UPDATE_INTERVAL_SECONDS=1.5
```

Keep the existing parser tuning unless there is a reason to change it:

```env
MAX_PAGES_PER_CATEGORY=500
PAGE_DELAY_SECONDS=0.7
STOP_AFTER_EMPTY_TODAY_PAGES=2
INCREMENTAL_STOP_AFTER_KNOWN_PAGES=2
INCREMENTAL_MIN_KNOWN_RATIO=0.80
INCREMENTAL_MIN_PAGES=2
INCREMENTAL_HEAD_SIZE=8
INCREMENTAL_OVERLAP_PAGES=1
```

## Important operational notes

- Do not multiply polling bot replicas while using this architecture. Keep one Telegram polling process and scale the parser/database architecture later.
- The parser only reads public Kleinanzeigen pages and does not implement CAPTCHA/auth/access-control bypass.
- The cache reduces repeated requests, but `PAGE_DELAY_SECONDS` should still stay reasonable.
- With SQLite, start with 3 workers. More workers are not automatically faster and can increase DB contention/network pressure.

## Next planned step

v2.7 / v3.0: PostgreSQL persistence and a durable DB-backed queue/leases. That will allow safe restarts and, later, separate worker Railway services without duplicating category work.
