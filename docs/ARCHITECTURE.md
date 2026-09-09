# DT Parser architecture

This document describes the active v4.23.x system. Historical 15+15-page Context
Radar and DT AI Lab designs are archived and are not runtime contracts.

## Runtime entrypoints

Railway starts `service_launcher.py`. It resolves the service role from
`DT_SERVICE_ROLE` or the Railway service name and replaces itself with the matching
Python entrypoint. The default role is `bot.py`.

The Telegram bot owns four FIFO user-scan lanes in stable single-service mode. A user
scan accepts at most two categories. Each job receives an isolated browser context;
the process may share one Chromium runtime.

Optional Page, Date, View, Lifecycle and Vinted workers use dedicated entrypoints and
queues. The old AI Worker role is intentionally routed to `retired_ai_worker.py` and
does not score or publish anything.

## User scan pipeline

The active scan path is:

`category/date pages -> card integrity -> exact views -> PostgreSQL -> filters/export`

Verified pages and date hints use versioned cache payloads. Cached listing IDs, URLs,
page identity and category redirects are revalidated before reuse. Partial scans do
not become a verified zero and are not stored as final shared cache results. One
bounded fresh-context recovery pass reuses strong PostgreSQL checkpoints before the
user sees a partial result.

## Kleinanzeigen Radar 3.2

AutoScan reads up to 20 verified pages per eligible product category for today only.
The first exact counter creates a baseline and contributes zero score. AutoScan is
the sole source of shared Radar baselines. User scans retain their own saved exact
counters and optional observation plans but cannot create or re-arm RadarObservation
rows.

Radar then owns its measurements. Due observations are leased with PostgreSQL
`FOR UPDATE SKIP LOCKED`, refreshed with exact counters and evaluated in a two-pass
category cohort. The active evidence window is six hours; confirmed catalogue rows
can remain visible for up to 48 hours, subject to current freshness and integrity
rules.

The live stages remain category-relative:

- noise below 3 views/hour;
- Candidate at P90;
- Early/Score at P95;
- Strong at P98;
- Hot at P99 with confirmation requirements.

Promotion, price reduction, dirty identity, missing URL and other unrefreshable rows
are excluded rather than recycled through the checkpoint queue. First measurements,
unknown counters and lifecycle disappearance never manufacture Score or Hot.

## Storage and isolation

PostgreSQL is authoritative in production; SQLite is supported for local tests.
Redis coordinates optional distributed worker streams and traffic limits, but the
main stable parser remains usable in its pinned local-lane profile.

Kleinanzeigen and Vinted use separate worker queues and database tables. Vinted exact
metrics remain fail-closed: missing or identity-mismatched values are UNKNOWN, not
zero.

## Safety contracts

- schema changes are additive and startup migrations are serialized;
- Radar startup maintenance does not delete Radar evidence tables;
- public traffic has bounded concurrency, cooldowns and watchdogs;
- exact-view results are bound to the requested listing identity;
- generated artifacts, secrets and local databases are ignored by Git;
- CI runs compilation, runtime-global analysis, release smoke checks and pytest.
