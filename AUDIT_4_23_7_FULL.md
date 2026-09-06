# DT PARSER v4.23.7 — Full Audit Report

Audit base: exact reconstructed v4.23.6 repository (full v4.22.6 + every patch v4.22.7 → v4.23.6).

## Scope

The audit covered the complete current repository, not only the last Vinted/Radar patch:

- Telegram Parser/Bot and callback/UI paths;
- Kleinanzeigen Parser, DT Radar 3.2 AutoScan, Radar observations and ranking;
- Page / Date / View distributed workers and Redis coordination;
- PostgreSQL models, additive startup migrations and write paths;
- traffic/adaptive throttling, scan priority and shutdown/task lifecycle;
- Lifecycle/Fast Sold and retired AI service routing;
- Vinted Lab, Vinted Scan/Metrics/Session workers, Radar 1.0 and local session helper;
- version/release contracts, service launcher routing and all repository tests;
- obvious secret logging, shell execution, unsafe eval/pickle patterns and synchronous blocking calls in async runtime paths.

Repository size at audit time: 136 files. Test suite: 34 test modules plus subtests.

## Validation result

Final v4.23.7 validation:

- recursive Python compile: PASS;
- runtime global-symbol audit: PASS;
- release smoke: PASS;
- pytest: **253 passed + 167 subtests passed**;
- no failed tests;
- no raw BOT_TOKEN / DATABASE_URL / REDIS_URL logging found;
- no shell=True / os.system runtime use found;
- no bare `except:` blocks found;
- synchronous `time.sleep` appears only in the standalone local Vinted session-capture utility, not in the Telegram async runtime.

A local SQLite `init_db()` execution could not be run in the audit container because that container does not have `aiosqlite` installed. This is not a repository dependency defect: `requirements.txt` pins `aiosqlite==0.21.0`. PostgreSQL/Railway network behavior cannot be reproduced offline by this audit.

## Real defects found and fixed

### 1. v4.23.6 Idle Turbo x8 was effectively clamped back to normal x3

v4.23.6 calculated an x8 request, then clamped it to `TRAFFIC.base_view_limit`, whose normal default is 3. The UI/release could therefore say Idle Turbo while actual local exact-view concurrency remained approximately normal.

v4.23.7 moves the burst authorization into `AdaptiveTrafficManager` and introduces the dedicated `autoscan_idle` priority. AutoScan may borrow up to `RADAR_AUTOSCAN_IDLE_VIEW_CONCURRENCY` (default 8) only while it is the sole registered scan job and there is no refusal penalty/cooldown. When a foreground user scan appears, new Turbo leases immediately fall back to the normal limit; already-running short requests are allowed to finish.

A behavioral async test proves eight concurrent idle leases are actually admitted, and another proves a foreground user reduces new admissions back to the normal view limit.

### 2. View Worker outer watchdog could throw away already completed exact-view shards

The AutoScan exact-view phase wrapped the entire remote View Manager call in `asyncio.wait_for(240s)`. If three shards had completed and one shard was still slow at the deadline, cancellation happened before the caller received the completed shard map. The next recovery stage could then treat the entire category as unresolved and redo far more work than necessary.

v4.23.7 makes the deadline an internal `RemoteViewManager` responsibility. At deadline it cancels only unfinished shards, preserves/merges all completed exact results, and returns them to AutoScan. Direct salvage and exact-tail repair then work only on genuinely missing URLs.

The same behavior is used by the short idle second-pass retry. A behavioral test proves completed shards survive a deadline while slow siblings are cancelled.

### 3. Release QA had drifted from the actual product version/semantics

The reconstructed v4.23.6 code compiled, but its own release smoke/static contracts still expected v4.23.5 and several Vinted tests asserted obsolete source-code strings rather than current behavior. Initial exact-v4.23.6 test result was 244 passed / 167 subtests / 6 failed, with these failures caused by stale release-test contracts.

v4.23.7 updates those contracts to test the current behavior and adds hardening tests for the two runtime defects above. Final result is 253 passed / 167 subtests / 0 failed.

## Areas reviewed with no blocking defect found

- DT Radar 3.2 score/evidence rules and organic fail-closed behavior remain intact.
- UNKNOWN exact counters remain NULL; no approximate or stale value is promoted to exact.
- AutoScan hard stop and category watchdog remain wired.
- background Radar maintenance does not share the foreground detail lock introduced by the old deadlock path.
- Page/Date cache schemas and runtime namespaces remain aligned between managers/workers.
- Redis distributed locks/leases retain expirations and crash self-healing behavior.
- PostgreSQL additive migration identifiers used in f-string DDL are static code dictionaries, not user input.
- Vinted exact metric paths remain fail-closed and Vinted Radar scoring stays off the Telegram event loop.
- Vinted Lab cache/watcher changes from v4.23.4/v4.23.5 remain present.
- service launcher routes Bot, Page, Date, View, Lifecycle and all Vinted worker roles independently.
- shutdown path cancels/gathers long-lived bot tasks and closes managed resources.

## Non-blocking production risks to watch

These are not release blockers, but they should be watched in Railway telemetry:

1. Page Worker idle prefetch still shares the same FIFO Redis page stream. User scans have bounded fallback behavior, so this is primarily a latency/fairness risk rather than a correctness defect. If foreground latency rises under heavy Radar prefetch, the next infrastructure step should be separate foreground/background Page queues.
2. A View Worker replica normally executes one active job at a time. Priority is carried in payloads, but an already-running large shard is not preempted. v4.23.7 reduces the impact by keeping shards bounded and preserving partial results; true preemption would require a worker-queue redesign.
3. Vinted full-market tables will continue growing. Existing retention and indexes are functional, but real PostgreSQL `EXPLAIN ANALYZE` on production-sized data is the right next step if Vinted Lab latency grows again.

## Accuracy / behavior deliberately unchanged

This hardening release does **not** loosen DT Radar admission, Organic Detail Gate, 400+ baseline behavior, 99% exact coverage policy, soft exact tail, DT Demand Score/Radar 3.2 rules, Vinted Like Momentum/Vinted Score, or subscription/payment logic.
