# DT PARSER v4.23.8 — First-Pass Recovery & Radar Self-Heal

**Base:** v4.23.7 Full Audit Hardening.

This release fixes the pattern observed in production where a normal Kleinanzeigen scan could finish partial on the first launch and then succeed immediately after pressing Repeat, while DT Radar accumulated many `допроверка` categories despite zero system errors.

## Root cause 1 — normal scan asked the user to perform the missing recovery pass

Stable production mode already had strong per-page retries and one BrowserContext recycle, but it explicitly forced:

- `SCAN_CATEGORY_ATTEMPTS = 1`
- `SCAN_AUTO_RECOVERY_PASSES = 0`

So a structurally partial category was exposed to Telegram immediately. Pressing Repeat created a new launch with a fresh browser context while PostgreSQL reused already verified page checkpoints. That second launch was effectively the recovery pass the first launch should have performed itself.

v4.23.8 keeps the cheap page retries, then gives a partial category one bounded fresh-context checkpoint-aware control pass **inside the same user scan**. Unexpected transient exceptions also get one clean second attempt. Strong verified pages are reused; this is not a blind re-scan from page 1.

If recovery still cannot prove the category, the UI remains fail-closed and the fallback button now retries **only incomplete categories**, not the whole original selection.

## Root cause 2 — Radar persisted retryable partials too early

AutoScan had the same architectural gap: after page/date or materially incomplete exact-view evidence remained partial, the category was immediately persisted into `допроверка` and only a later separate Retry round could recreate the context and finish it.

v4.23.8 gives retryable Radar categories one bounded inline self-heal pass before persisting review:

- retryable: structural page/date partials and materially incomplete exact views;
- retryable transient exceptions: temporary Kleinanzeigen access failures/timeouts;
- fresh BrowserContext + category parser reset before repair;
- verified PostgreSQL checkpoints are reused;
- if a foreground user scan appears, the inline repair is skipped/yielded so user scans keep priority;
- system failures, category watchdogs and Organic Detail Gate UNKNOWN are **not** blindly accepted/replayed.

When the repair succeeds, the category counts as successful in the same circle and `inline_recovered` is recorded instead of creating a review entry.

## Better review diagnostics

The previous admin card grouped many transient transport reasons under `❓ другое`. v4.23.8 adds an explicit:

`🌐 transport N`

bucket and can rebuild the breakdown for already-persisted v4.23.6/v4.23.7 failure records from their saved reasons.

The resource line is also corrected. With `активных 0 · очередь 0`, a Traffic Manager penalty/cooldown is no longer shown as `приоритет пользователей`; it is displayed as:

`🧯 защитный режим Kleinanzeigen`

with recent refusal telemetry. This makes HTTP 403/429/site-pressure visible instead of looking like phantom user load.

## Safer idle acceleration

Idle Page Worker prefetch is reduced from the whole **20-page** category to a rolling **16-page** window. Idle Radar remains faster than normal mode, but avoids queueing the entire category burst at once when Kleinanzeigen is already sensitive to request pressure.

Idle exact-view Turbo from v4.23.7 remains unchanged (up to x8 when healthy and no foreground users).

## Accuracy is unchanged

This release adds recovery **before** fail-closed review; it does not lower evidence standards.

- minimum exact-view coverage remains **99%**;
- soft exact tail remains max **8**;
- unresolved exact counters remain `NULL` / UNKNOWN;
- Organic Gate remains strict;
- 400+ inherited-view protection remains unchanged;
- DT Radar 3.2 score/admission rules remain unchanged.

## Deployment

Apply over **v4.23.7**.

Required: redeploy **Parser / Bot**.

Page/Date/View Workers have no functional code change in this patch and may remain running. Redeploying them from the same checkout is safe but not required. Vinted workers are unchanged.

No manual SQL migration. No new required Railway variables.

For a clean production comparison, start a new full Radar circle after deploy. Existing review records remain preserved and their diagnostic breakdown can be reclassified by the new UI.
