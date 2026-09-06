# DT PARSER v4.23.8 — First-Pass Recovery Incident Audit

## Production symptom

Two independent symptoms were reported on the same production stack:

1. a normal user scan for 05.09.2026 with 2 categories ended partial in ~17 seconds with 0 confirmed listings, then completed successfully immediately after pressing Repeat;
2. DT Radar AutoScan showed 5 successful / 13 review categories after 18 processed, with 0 system errors, while the old review breakdown showed 2 page-related and 11 generic `other` failures.

## Confirmed user-scan root cause

The stable bot configuration forced both:

- `SCAN_CATEGORY_ATTEMPTS = 1`
- `SCAN_AUTO_RECOVERY_PASSES = 0`

The page engine did have bounded per-page retry and one context recycle. However, once the **category result itself** remained structurally partial, the first Telegram launch had no category-level control pass left.

The manual Repeat path created a new job/parser BrowserContext and reused verified PostgreSQL page checkpoints. Therefore a transient partial could succeed immediately on the second Telegram launch even though the underlying data was available all along.

This is an orchestration defect: the recovery action was exposed to the user instead of being executed once automatically before declaring the launch partial.

## Confirmed Radar orchestration gap

Radar used the same one-category-at-a-time parser and bounded page/view recovery, but after `_category_pipeline()` returned a retryable `partial` or `radar_views` verdict, `_run_radar_autoscan_round_inner()` persisted that category directly into `failed_categories` / review telemetry. A fresh-context checkpoint-aware second category pass happened only later in a separate retry round.

v4.23.8 inserts one bounded inline repair before persisting review. It reuses verified checkpoints and yields if a foreground user appears.

## Why old Radar UI could show `priority users` with 0 active / 0 queued

The old resource line used `_radar_autoscan_idle_turbo_available()` as a binary choice. That helper also returns false when the Traffic Manager is in refusal penalty/cooldown. Therefore the UI fell into the `priority users` label even when there were zero users.

v4.23.8 distinguishes:

- real foreground users;
- Kleinanzeigen refusal/cooldown protection mode;
- healthy Idle Turbo;
- ordinary safe mode.

## Why `другое` was too large

The v4.23.7 review classifier had explicit page/view/watchdog/gate buckets. Temporary HTTP 403/429, limits and timeouts could still be persisted as `partial` and fall into the generic bucket depending on their exact reason string/history version.

v4.23.8 adds a dedicated `review_transport` counter and reclassifies old persisted failure reasons when that new counter is absent.

## Changes

### Normal user scans

- stable exception attempts: 1 -> 2;
- stable structural recovery passes: 0 -> 1;
- fresh BrowserContext before exception retry and structural recovery;
- parser category state reset before the control pass;
- verified PostgreSQL checkpoints retained and force-refreshed only where required;
- partial fallback button now retries only incomplete categories.

### Radar

- one inline repair for `partial` / `radar_views` before review persistence;
- transient access/timeouts can receive that same fresh-context repair;
- inline repair yields to foreground users;
- successful inline repairs are counted separately;
- transport review bucket added;
- resource telemetry distinguishes site pressure from user priority;
- idle Page Worker prefetch default reduced from 20 to 16 to avoid warming the entire category in one burst.

## What remains fail-closed

v4.23.8 does not weaken evidence acceptance:

- exact-view minimum coverage: 99%;
- soft exact tail max: 8;
- unresolved views remain NULL/UNKNOWN;
- Organic Gate UNKNOWN does not get silently bypassed;
- system failures/watchdogs are not converted to success;
- DT Radar 3.2 score/admission and 400+ inherited-view protection remain unchanged.

## Validation

On the reconstructed complete v4.23.8 repository:

- recursive Python compile: PASS;
- release smoke: PASS;
- pytest: 261 passed;
- subtests: 167 passed;
- failures: 0.

A second clean installation test is performed by overlaying the v4.23.8 patch on the reconstructed exact v4.23.7 tree and re-running compile/smoke/tests.
