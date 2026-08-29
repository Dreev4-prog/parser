# DT PARSER v4.15.8 — AutoScan Deadlock & Hard Stop

**Base:** v4.15.7 Verified Organic Velocity.

v4.15.8 is a runtime-correctness release for DT Radar AutoScan. It does **not** change Organic Demand rules, the 400+ baseline rule, or DT Demand Score weights.

## 1. Root cause fixed: background Detail Gate lock inversion

v4.15.7 used one process-global Radar detail lock/parser for both:

- foreground AutoScan / Radar admission;
- background Bump Resurrection sweep / Verified Organic Velocity checks.

With `TRAFFIC_BACKGROUND_VIEWS_DURING_SCANS=0`, a race was possible:

1. background work acquired the shared detail lock;
2. AutoScan started a category and registered a foreground scan job;
3. the background task then waited for a background view lease that is intentionally blocked during scans;
4. AutoScan later reached Organic Detail Gate and waited for the lock held by that background task.

That is a real lock inversion and explains a round remaining at `0/84` indefinitely.

v4.15.8 splits detail verification into two isolated lanes:

- foreground detail lock + parser;
- background detail lock + parser.

Background work can no longer hold a resource that foreground Radar admission requires.

## 2. Background maintenance pauses for the whole AutoScan round

`AdaptiveTrafficManager` now has an explicit low-priority background pause counter.

When an AutoScan round enters its runner:

- no new background Radar sweep view/detail request may start;
- no new 400+ Verified Organic Velocity checkpoint may start;
- already leased short requests may finish;
- foreground scan/view traffic remains available.

The pause is always released when the round finishes, is stopped, or the runner exits with an error.

The Bump Resurrection sweep also checks the traffic snapshot before every family/listing and yields when foreground work starts. The maintenance scheduler defers historical sweep/backfill entirely while user scans or AutoScan are active. An interrupted sweep is **not** allowed to mark the one-time sweep complete.

## 3. Real hard Stop

The admin button is now:

`⏹ Остановить сейчас`

Pressing it:

1. persists `status=paused` immediately in PostgreSQL, so a Railway restart cannot resurrect the round;
2. signals a process-local stop event;
3. cancels the currently owned AutoScan category task;
4. releases `scan_job_started()` accounting in `finally`;
5. resets the category browser context with a bounded cleanup;
6. keeps `current_index` unchanged.

Therefore Resume starts the interrupted category again from a clean state instead of advancing past an incomplete result.

Stop also interrupts:

- waiting for user scans;
- partial/system cooldown;
- success gap.

It no longer waits for the current category to finish.

Already dispatched short Page/Date worker probes may finish in their own Railway worker, but AutoScan no longer waits for them and does not start another category while paused.

## 4. Full-category watchdog

AutoScan now owns a hard watchdog around the **entire category pipeline**:

`Date/Page scan -> exact views -> Organic Detail Gate -> Radar admission`

Default:

`RADAR_AUTOSCAN_CATEGORY_TIMEOUT_SECONDS=480` (8 minutes)

If the watchdog fires:

- the category task is cancelled;
- the category is stored as `⚠️ допроверка`;
- the parser/browser session is recycled;
- the round advances to the next category instead of freezing all 84 categories.

This is separate from the normal user-scan watchdog and does not change user scan behavior.

## 5. Dedicated View Worker wait is bounded for AutoScan

The generic View Manager can historically wait much longer for a healthy-but-stuck remote batch. AutoScan now adds its own exact-recovery watchdog:

`RADAR_AUTOSCAN_VIEW_RECOVERY_TIMEOUT_SECONDS=240` (4 minutes)

If it expires:

- already obtained direct exact counters are preserved;
- unresolved counters remain unknown;
- the category fails closed as incomplete and goes to retry;
- AutoScan does not wait 30 minutes inside one category.

## 6. Live AutoScan stage in admin

`0/84` only means zero categories have fully completed. The admin card now also exposes the live phase of category 1:

- `🔎 поиск даты · запросов N · стр. X`
- `📄 страницы X/15 · объявлений N`
- `👁 точные просмотры X/N`
- `⚙️ Organic detail-check`

The card also shows the configured category watchdog. This removes the false impression that nothing is happening while the first category is actively working.

## 7. Verified Organic Velocity is unchanged

v4.15.7 behavior remains exactly intact:

- the 400 threshold applies only to the first exact counter DT observes;
- first observation `>=400` contributes zero inherited views to Score;
- two later clean exact checkpoints at least 30 minutes apart are required;
- after certification only `current_views - baseline_views` is used;
- high views alone never mark an ad promoted.

DT Demand Score remains:

- 40% Relative View Velocity
- 20% Acceleration
- 15% Persistence
- 15% Repeatability
- 10% Price Fit

## 8. Database / Railway

No manual SQL migration is required.

No new required Railway variable is required. Optional overrides:

```text
RADAR_AUTOSCAN_CATEGORY_TIMEOUT_SECONDS=480
RADAR_AUTOSCAN_VIEW_RECOVERY_TIMEOUT_SECONDS=240
```

Recommended deployment from the same v4.15.8 checkout:

1. **Parser — required.** This is where the deadlock, hard stop, AutoScan watchdog and admin live stage are fixed.
2. **AI Worker — recommended** for one-version consistency; scoring semantics are unchanged.
3. **Lifecycle Worker — recommended** for one-version consistency.
4. **Page Worker / Date Worker — recommended** from the same checkout; their parsing algorithms are unchanged.
5. **View Worker — recommended** from the same checkout; its algorithm is unchanged, while Parser now bounds how long AutoScan waits for it.

## 9. Expected startup log

After deploy Parser should include:

```text
[service-launcher] version=4.15.8 service='parser' ...
Starting @DTTEAM_PARSER_BOT | version=4.15.8 ...
v4.15.8 AutoScan Deadlock & Hard Stop online | category_watchdog=480s | view_recovery_watchdog=240s | background_pause=round | detail_lanes=foreground+background
```

## 10. Smoke checks

1. Start AutoScan: the panel should show category `Autos` plus a live stage instead of only static `0/84`.
2. While AutoScan is running, Bump Resurrection / Verified Organic Velocity should stop issuing new background detail/view requests from Parser.
3. Press `Остановить сейчас` during Date/Page: status becomes paused and category does not advance.
4. Resume: the same interrupted category starts again.
5. Press Stop during exact views: the View Manager task is cancelled; remote View Worker receives its existing cancellation key when applicable.
6. Press Stop during Organic Detail Gate: foreground detail task is cancelled and the foreground lock is released.
7. Simulate a category longer than 480s: it becomes `⚠️ допроверка`, parser is recycled, next category starts.
8. Simulate a remote exact recovery longer than 240s: unresolved views stay unknown and category goes to retry.
9. Background sweep interrupted by AutoScan must not set `dt_radar_v4156_bump_sweep_complete=1` merely because the partial batch had zero UNKNOWNs.
10. First-seen `399` / `400` / `942` / `16,337` Verified Organic Velocity semantics must remain identical to v4.15.7.
