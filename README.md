# DT Parser v4.8.0 — Performance Core

Production-clean release based on the **v4.6.0 parser core**, retaining the later RU/EN UI, admin operations center and DT AI Lab.

## What changed in parser performance

The chronology/page/view algorithms stay v4.6.0. This release removes artificial fleet stalls around them:

- Date/Page/View keep fleet-wide concurrency caps, but **403/429 cooldown is replica-local**. One refusal cannot freeze all four replicas.
- Date and Page keep a shared aggregate search concurrency budget, but no longer share a refusal cooldown.
- Worker runtime queues use schema `perf480`; stale jobs/pending locks from older releases are ignored automatically. Useful Date predictor/cache data is preserved.
- Fresh Redis stream messages are consumed before `XAUTOCLAIM` crash-recovery messages.
- Page cache wait is reduced from 1800 ms to 900 ms; local stable fallback remains authoritative.
- Large view batches shard against the expected four-replica fleet even if one heartbeat is late at batch creation.
- View browser fallback remains narrow but allows up to 2 concurrent browser fallback leases across the fleet.
- Admin worker screens now expose average local traffic wait and Redis-limiter wait.

## Intentionally NOT included

- no 10-minute Chromium idle shutdown;
- no scan-fleet wake-up/prewarm;
- no extra HTTP retry/session-recovery experiments;
- no changes to date correctness, regional chronology verification, page quality gates or PostgreSQL result semantics.

## Retained product/UI features

- Russian / English user interface;
- first-start language selection + Settings language switch;
- current admin panel + “Кто сейчас парсит”;
- DT AI Lab unread badge and Russian AI labels;
- Product Opportunity Engine;
- Fast UI navigation.

## Railway

Same services and root command:

```text
python service_launcher.py
```

No new required Railway variables and no PostgreSQL migration. `WORKER_RUNTIME_SCHEMA` is optional; default is `perf480`.
