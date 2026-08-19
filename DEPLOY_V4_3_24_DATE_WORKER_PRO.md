# DT PARSER v4.3.24 — DATE WORKER PRO

## What changed

- New dedicated `Date Worker` Railway role.
- Recommended: 2 Date Worker replicas.
- Date search uses parallel remote chronology probes as an acceleration hint.
- The stable main parser always revalidates the final boundary locally before accepting the date.
- Redis Date Cache / single-flight: 180 seconds.
- Date selection is limited to the last 7 calendar dates (today + previous 6 days).
- The date picker shows all 7 allowed dates directly.
- Old Page Worker x2, View Worker x2, View Sharding and exact views are unchanged.
- If Date Worker is offline/weak/inconsistent, the existing local stable date locator runs automatically.

## Railway

Keep the existing services unchanged:

- Main Bot
- Page Worker — 2 replicas
- View Worker — 2 replicas
- Redis
- PostgreSQL

Create one additional service from the SAME GitHub repository:

### Date Worker

Service name:

```text
Date Worker
```

Required variable:

```text
REDIS_URL=${{Redis.REDIS_URL}}
```

Set:

```text
Replicas = 2
```

No `DATABASE_URL` and no `BOT_TOKEN` are needed in Date Worker.

The common `service_launcher.py` detects the service name and launches:

```text
python date_worker.py
```

If the Railway service has another/custom name, add this variable to that service:

```text
DT_SERVICE_ROLE=date-worker
```

## Default tuning

No extra variables are required. Defaults:

```text
DATE_WORKER_CONCURRENCY=2
DATE_CACHE_TTL_SECONDS=180
DATE_PROBE_TIMEOUT_SECONDS=10
DATE_MAX_AGE_DAYS=6  # fixed product limit
```

`DATE_MAX_AGE_DAYS=6` means today plus the previous six days = seven selectable calendar dates.

## How date search works

The Date Manager initially distributes checkpoints like:

```text
1, 2, 4, 8, 16, 32, 50
```

across the Date Worker replicas. As soon as a chronology bracket is found, it may run one small parallel refinement round. The returned boundary is only a hint.

The main stable parser then checks the likely boundary and adjacent pages locally. Only locally verified exact target-day evidence is accepted. If verification fails, the unchanged local exponential/binary locator is used.

## Admin panel

Open:

```text
Admin -> 📅 Date Worker
```

It shows worker count, queue, active probes, probes/sec, cache hits, last boundary and 403/429 counters.

## First test

1. Deploy v4.3.24 to GitHub.
2. Create `Date Worker` with `REDIS_URL` and 2 replicas.
3. Wait until `Admin -> 📅 Date Worker` shows `🟢 online · workers: 2`.
4. Run one scan with 3-4 categories using yesterday or 2-4 days ago.
5. Watch both Date Worker replicas during the date phase.
6. Confirm Page Worker and View Worker continue working unchanged afterward.
