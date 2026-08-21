# DT Parser v4.7.1 — View Fleet Warmup & Fast Path

Production-clean GitHub package.

## View Fleet Warmup & Fast Path v4.7.0

This release fixes two cold-start/slow-view failure modes without changing view accuracy:

- the first accepted scan broadcasts a lightweight HTTP prewarm event to every View Worker; no Chromium is launched;
- the View Worker keeps its public HTTP/TLS session alive longer, so the view phase does not start from a cold connection pool;
- large batches are always cold-safe sharded for the intended 4-replica fleet. A batch no longer becomes one indivisible 1000-URL job just because only one worker heartbeat was visible at dispatch time;
- if no View Worker heartbeat is visible at the exact start of the view phase, the bot waits up to 8 seconds for the fleet before falling back locally;
- if many official-counter requests miss together, the worker warms one normal public HTTP session and retries the official counter before opening rendered Chromium pages;
- per-item transient retries are reduced to one by default, avoiding retry storms;
- admin View Worker telemetry now shows live/expected replicas plus fast-path misses, HTTP-session recoveries and Chromium fallbacks.

No Railway variable is required. Optional overrides:

- `VIEW_EXPECTED_REPLICAS=4`
- `VIEW_WORKER_READY_WAIT_SECONDS=8`
- `VIEW_HTTP_WARM_TTL_SECONDS=300`
- `ACCURATE_VIEW_SESSION_RECOVERY_MIN_MISSES=8`
- `ACCURATE_VIEW_SESSION_RECOVERY_MIN_RATIO=0.08`

The fleet-wide official-counter budget remains capped at 16 and existing 403/429 adaptive cooldown remains active.


## Railway

All services use the same repository and root `railway.json`:

```text
python service_launcher.py
```

`service_launcher.py` selects the current role for Bot / Date Worker / Page Worker / View Worker / AI Worker from the Railway service name (or `DT_SERVICE_ROLE` if explicitly configured).

## Language

A new user chooses a language on the first `/start`:

- 🇷🇺 Русский
- 🇬🇧 English

The choice is saved per user and can later be changed in `⚙️ Настройки / Settings → 🌐 Язык / Language` or with `/language`.

The normal user interface follows the saved language even for admin accounts. The actual admin panel remains Russian.


## Fast UI

- media-menu callbacks no longer waste a Telegram API round-trip on an invalid `edit_text`;
- the main menu reuses Telegram `file_id` after the first image upload;
- independent PostgreSQL reads are parallelized on the home screen, subscription screen and scan start checks;
- `Мои сканы / My scans` performs one archive sweep instead of two and loads list/count in parallel;
- callback spinners are acknowledged before non-critical database reads where safe.

## Included production features

- Product Opportunity Engine
- DT AI Lab
- AI Lab badge notifications
- Idle Chromium memory release after complete fleet idle
- Admin workers/active parsing center
- Russian / English user interface

This clean package intentionally excludes historical deploy notes, tests, Python bytecode caches, and legacy worker entrypoints that are not used by the current production service launcher.


## Instant UI v4.6.8

- user settings and selected categories use a short process-local presentation cache;
- My Scans and Popular menus use a 3-second UI cache;
- `last_seen` bookkeeping runs outside the callback critical path;
- My Scans no longer performs an archive UPDATE on every open; the background sweeper runs every minute;
- repeated RU→EN presentation translations are memoized;
- no parser, worker, Redis queue, AI scoring or Chromium behavior changed.

Optional tuning (defaults are already recommended):
- `UI_STATE_CACHE_TTL_SECONDS=20`
- `UI_SCAN_MENU_CACHE_TTL_SECONDS=3`


## View Fast Recovery v4.6.9

- temporary official-counter `403/429/5xx/timeout` failures are retried through the adaptive HTTP lane before Chromium fallback;
- a short deterministic retry jitter avoids a four-replica retry burst after the shared cooldown;
- genuine Chromium fallback is limited to two navigations fleet-wide instead of one;
- a cold Chromium runtime is started only after the worker acquires a browser traffic lease, preventing all replicas from warming browsers at once;
- exact-view rules are unchanged: no guessed counters are accepted;
- idle Chromium shutdown remains 10 minutes and no parser/AI/UI behavior is changed.

Optional tuning (recommended defaults are built in):
- `ACCURATE_VIEW_TRANSIENT_HTTP_RETRIES=1`
- `ACCURATE_VIEW_TRANSIENT_RETRY_JITTER_MS=150`


## v4.7.1 — Scan Fleet Wake-up

- First accepted scan broadcasts one debounced wake-up wave to Date/Page/View fleets.
- Page replicas launch shared Chromium before their first page job, without navigating Kleinanzeigen.
- View replicas prewarm HTTP within ~0.5 s and keep v4.7.0 cold-safe sharding.
- Date replicas only acknowledge readiness; no extra site request and no Chromium launch.
- Idle browser shutdown remains 10 minutes after the whole fleet becomes empty.
- BrowserIdleShutdownGuard now protects prewarm from a shutdown race at the 10-minute boundary.
- AI Worker remains intentionally outside foreground scan wake-up so it cannot steal scan resources.
