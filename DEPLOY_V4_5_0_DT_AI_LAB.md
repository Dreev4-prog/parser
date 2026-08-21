# DT Parser v4.5.0 — DT AI Lab / Early Winner Engine

v4.5.0 adds an **admin-only shadow AI layer** on top of the stable v4.4.0 parser. User-facing parsing, exact date verification, exact views, Page Worker, Date Worker and the existing +3/+6/+12 user auto-measurement toggle keep their previous behavior.

## What Early Winner does

After a normal completed scan, a separate `AI Worker` reads the already-saved scan snapshot from PostgreSQL. It does not wait in the user's scan path and does not open a browser.

For fresh listings with an exact publication clock it estimates an initial Early Winner score using:

- views per hour at discovery;
- velocity percentile against the same deterministic product identity when at least 3 close peers exist in the scan; otherwise the category cohort is used;
- current view level;
- listing freshness;
- product identity confidence;
- price versus the recent median of the same deterministic `identity_key` when enough history exists.

The engine is currently `ew-stat-v1`: a transparent statistical shadow model. Every prediction and reason is stored so its real accuracy can be measured before any user-facing release.

After the first checkpoint the live score also looks at the **most recent interval speed** (+1→+3, +3→+6), so acceleration and deceleration affect the score instead of relying only on the lifetime average. Evidence confidence increases as real checkpoints arrive.

Stages:

- `WATCH` — early candidate;
- `RISING` — stronger growth signal;
- `EARLY WINNER` — strongest current signal;
- `CONFIRMED` — future observed growth confirms the signal;
- `REJECTED` — +6h future growth did not confirm it.

## Independent candidate checkpoints

The user's `Автозамеры` switch is **not changed and not required** by Early Winner.

Only a small candidate set (default up to 10/category, 20 visible candidates/scan plus a tiny control sample) receives internal `+1h / +3h / +6h` checkpoints.

Before any request, AI Worker reuses a fresh exact value already present in PostgreSQL. This may come from:

- another scan;
- the user's normal auto-measurement;
- a manual refresh;
- another Early Winner checkpoint.

If no fresh exact value exists, AI Worker delegates the tiny batch to the existing View Worker fleet. It never opens Chromium itself.

### Load protection

By default `AI_PAUSE_DURING_USER_SCANS=1`.

If any user scan is queued/running, AI can still reuse fresh PostgreSQL measurements, but it **does not start new Kleinanzeigen view requests**. Missing AI checkpoints wait until the user scan load clears. Thus interactive parsing remains higher priority.

## Admin panel

`/admin -> 🧠 DT AI Lab` shows:

- scans analyzed today;
- listings seen and fresh/eligible listings;
- WATCH / RISING / EARLY WINNER funnel;
- confirmed / rejected outcomes;
- control-group size;
- pending +1/+3/+6 checkpoints;
- AI Worker heartbeat/status;
- Early Winner lists and detailed candidate cards;
- initial reasons, evidence confidence (not a probability), price median, forecasts and actual checkpoints;
- accuracy by score band and +3/+6 forecast-range hit rate.

High-signal shadow events (`EARLY WINNER` and `CONFIRMED`) are pushed only to `ADMIN_IDS`. Users see no AI controls in v4.5.0.

## Why there is a control group

A tiny set of listings just below the score threshold is also observed in shadow mode. They stay out of the normal Early Winner list. Their purpose is to measure false negatives: if low-score controls frequently become objective winners, the model needs recalibration.

## Railway

Keep the existing production fleet unchanged:

- Main Bot ×1
- PostgreSQL ×1
- Redis ×1
- Date Worker ×4
- Page Worker ×4
- View Worker ×4

Add **one** new service from the same GitHub repository:

- service name: `AI Worker`
- replicas: `1`
- start command remains `python service_launcher.py`
- `DATABASE_URL=${{Postgres.DATABASE_URL}}`
- `REDIS_URL=${{Redis.REDIS_URL}}`
- no `BOT_TOKEN` is required on AI Worker.

`service_launcher.py` detects `AI Worker` automatically and enables remote View Worker delegation inside that service.

No additional variables are required. Optional tuning:

```env
AI_EARLY_WINNER_ENABLED=1
AI_EARLY_MAX_AGE_HOURS=24
AI_EARLY_SCORE_FLOOR=65
AI_CANDIDATES_PER_CATEGORY=10
AI_TOTAL_CANDIDATES=20
AI_CONTROL_PER_CATEGORY=2
AI_CHECKPOINT_HOURS=1,3,6
AI_PAUSE_DURING_USER_SCANS=1
AI_REUSE_WINDOW_MINUTES=15
AI_INITIAL_BACKFILL_MINUTES=30
AI_OBSERVATION_LATE_GRACE_MINUTES=90
```

## Important safety boundary

v4.5.0 does **not** change:

- `parser.py` exact parser core;
- `date_manager.py` date logic;
- `date_worker.py`;
- `page_manager.py` / `page_worker.py`;
- `view_manager.py` / `view_counter_worker.py`;
- `traffic.py`;
- the 20-minute category watchdog;
- the user's existing auto-measurement semantics.

If AI Worker is offline, the normal parser continues operating exactly as before; only AI Lab stops receiving new shadow analysis/checkpoints.
