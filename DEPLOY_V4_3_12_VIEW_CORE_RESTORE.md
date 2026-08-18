# v4.3.12 VIEW CORE RESTORE

Fixes the v4.3.11 failure mode where a scan could finish after verifying only a tiny fraction of public view counters.

Accurate Views now uses three stages:
1. official s-vac-inc-get endpoint through the normal HTTP client;
2. the same official endpoint through Playwright BrowserContext.request with browser session/cookies;
3. a guarded rendered-page fallback only when both exact endpoint paths miss.

Rendered fallback is viability-probed and capped to avoid hundreds of slow Chromium navigations.
A scan with fewer than 80% exact view counters (for batches >=20) is marked partial instead of falsely successful.

Recommended Railway values remain:
MULTIUSER_LOCAL_WORKERS=4
CATEGORY_PIPELINE_GLOBAL_LIMIT=4
INLINE_VIEW_PHASE_GLOBAL_LIMIT=2
MULTIUSER_VIEW_POOL_SIZE=9
MULTIUSER_VIEW_MIN_INTERVAL_SECONDS=0.05

New settings are optional; defaults are built in:
ACCURATE_VIEW_CONTEXT_CONCURRENCY=4
ACCURATE_VIEW_BROWSER_FALLBACK_MAX=50
ACCURATE_VIEW_BROWSER_PROBE_ATTEMPTS=3
MIN_PRIMARY_VIEW_COVERAGE=0.80
