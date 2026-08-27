# DT PARSER 4.14.0 — Fast Sold Lifecycle

## Base
Directly based on **4.13.0 Simple Referral Promo**. Referral promo, Daily Radar fixes, AutoScan View Deadlock Recovery, the 84-category AutoScan policy, four foreground parser lanes and existing Page/Date/View/AI workers are preserved.

## What this release adds

### DT Radar — Fast Sold
A new full-access feed appears under `DT Radar -> Лучшие сейчас`:

- `⚡ Fast Sold / Быстро исчезли`
- shows strong listings that DT Radar first saw live and later confirmed unavailable during the first 3 hours;
- each row stores/uses approximate lifetime, disappearance time, price, last known views and Peak DT Score;
- product detail explains that Kleinanzeigen does not always distinguish a completed sale from a seller removing the listing.

### Lifecycle schedule
Only fresh strong Radar listings are watched (`DT Score >= 72`). Checks are anchored to the listing's DT `first_seen_at`:

- +15 min
- +30 min
- +60 min
- +120 min
- +180 min

A single unavailable result becomes only `confirming`. The worker checks the detail page again after ~3 minutes. Only a second unavailable result becomes `disappeared` / Fast Sold.

`403`, `429`, 5xx, timeouts and ambiguous responses are stored as `unknown` and retried gently; they never count as disappearance.

## Architecture

### New `Lifecycle Worker`
The worker is intentionally separate from the Telegram parser:

- no category parsing;
- no Page/Date/View worker queues;
- no browser fallback;
- only lightweight direct detail-page availability checks;
- default concurrency: 4;
- PostgreSQL table `radar_lifecycle_watches` is the durable queue;
- no Redis is required for this worker.

The queue uses row leases and PostgreSQL `FOR UPDATE SKIP LOCKED`, so a worker restart does not lose pending checks and future multiple replicas can avoid claiming the same row concurrently.

## Railway deployment

1. Deploy **4.14.0** to the repository/services as usual.
2. Confirm parser startup contains `version=4.14.0`.
3. Create **one** additional Railway service from the same repo.
4. Name it exactly `Lifecycle Worker`.
5. Give that service the same PostgreSQL `DATABASE_URL` used by Parser.
6. It does **not** need `BOT_TOKEN`. Redis is not required/used by Lifecycle.
7. Keep one replica initially.

`service_launcher.py` will detect the service name and run `lifecycle_worker.py`.

If you use another service name, add:

```text
DT_SERVICE_ROLE=lifecycleworker
```

No other new variables are required. Optional tuning exists (`LIFECYCLE_CONCURRENCY`, `LIFECYCLE_BATCH_SIZE`, `LIFECYCLE_POLL_SECONDS`) but the defaults are the recommended first launch profile.

## Expected logs

Lifecycle service startup:

```text
[service-launcher] version=4.14.0 service='Lifecycle Worker' role=lifecycle-worker target=lifecycle_worker.py ...
DT Radar Lifecycle Worker online | version=4.14.0 concurrency=4 batch=20 poll=10s
```

When Radar finds a strong fresh candidate:

```text
DT Radar Lifecycle queued external_id=... product=... score=... next=...
```

Normal live checkpoint:

```text
Lifecycle check watch=... external_id=... active=True status=watching ...
```

First missing check:

```text
Lifecycle check watch=... external_id=... active=False status=confirming ...
```

Confirmed disappearance about 3 minutes later:

```text
DT Radar Fast Sold confirmed external_id=... product=... lifetime=...s checks=...
Lifecycle check watch=... external_id=... active=False status=disappeared ...
```

## Smoke test

1. Open paid `DT Radar -> Лучшие сейчас` and confirm the new `⚡ Быстро исчезли` button is present.
2. Start/finish an AutoScan category with strong Radar signals.
3. In logs, confirm `DT Radar Lifecycle queued ...` appears for qualifying fresh listings.
4. Confirm Lifecycle Worker stays online and emits heartbeat logs.
5. After due checkpoints, verify `active=True` checks do not affect foreground parser jobs.
6. When a watched ad truly becomes unavailable, verify the first miss is only `confirming` and the second miss records `disappeared`.
7. Open `⚡ Быстро исчезли` and verify the product displays the approximate lifetime and disappearance timestamp.

## Database
`radar_lifecycle_watches` is created automatically with `CREATE TABLE IF NOT EXISTS` before the metadata pass, so simultaneous Railway service startup is safe. No manual migration is required.
