# DT PARSER v4.10.1 — DT Radar Reliability Fix

## Base

v4.10.1 is built directly on **v4.10.0 DT Radar**. No Radar feature is removed or rolled back.

This release merges two fixes discovered after v4.10.0 was created:

1. View Speed Fix
2. Repeated Page Recovery

## View Speed Fix

Large foreground view batches are always split into small Redis shards instead of depending on a momentary View Worker heartbeat count. With the default four-worker fleet, a 149-listing job is expected to become up to eight small jobs instead of one 149-URL worker job.

Transient official-counter `403/429` responses get one short exact HTTP retry before verified Chromium fallback. Exactness rules are unchanged. The fleet-wide official HTTP budget remains bounded, and Chromium fallback can run on up to two different worker replicas concurrently while each replica remains locally serialized.

Default runtime prefix for this release:

```text
dtparser:viewcounter:runtime:v4101
```

No new Railway variable is required.

## Repeated Page Recovery

A persistent `repeated-content` page no longer ends the whole category after the normal page retries and BrowserContext recycle.

The recovery path is intentionally narrow:

- repeated page contributes zero listings;
- repeated page contributes zero confirmed depth;
- scanner advances to the next nationwide page;
- later verified pages replace the missing depth;
- if the public nationwide window ends with a repeat-created shortfall, only the missing verified depth may be replaced from independent regional feeds;
- recovery is bounded by `DIRECT_REPEATED_RECOVERY_LIMIT` (default 3);
- `challenge`, `page-identity` and other persistent invalid classes remain strict failures.

## DT Radar retained

All v4.10.0 Radar behavior remains present:

- shared subscriber-only `📡 DT Radar`;
- Hot / Rising / Stable / Cooling / Historical;
- AI Picks;
- all-time ranking;
- categories;
- `⭐ Мой Radar`;
- persistent product/signal history;
- scan TOP-12 and DT AI signals feed the same product family;
- no extra Kleinanzeigen traffic is created by Radar bookkeeping.

## Four-lane queue retained

The v4.10.0 / v4.9.1 Four-Lane Guarantee is retained:

```text
users 1–4 -> parsing
user 5+   -> visible FIFO queue
```

Free-trial and paid users share those same four main lanes.

## Railway rollout

Deploy v4.10.1 to:

- `parser`
- `View Worker` (all 4 replicas)
- `AI Worker`

Date/Page workers contain no release-specific logic change, but deploying all services from the same repository/version is recommended so admin telemetry reports one version everywhere.

No destructive PostgreSQL migration is required. Existing DT Radar tables remain intact.

## Smoke test

1. Confirm startup shows `version=4.10.1` and four local scan workers.
2. Open `📡 DT Radar` and confirm existing Radar data/favorites are still present.
3. Start a today scan with 2 categories / 50 pages.
4. If a page repeats, expect a log like `Repeated page recovery skip ...` and the category should continue instead of immediately becoming partial.
5. On the view stage, a large batch should log `Remote view sharding ... shards=...`; View Worker jobs should be small shards rather than one 100+ URL job.
6. Verify the final view counts remain exact and Radar receives the completed scan TOP in the background.
