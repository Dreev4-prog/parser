# DT PARSER 4.22.3 — Vinted Session/OAuth Exact Metrics Probe

## Why this release exists

The live v4.22.2 Railway run returned 576 catalog rows but only 257 unique items from the global newest-first feed, while every anonymous `/api/v2/items/{id}/details` request was blocked with HTTP 403. Public item pages still matched identity and exposed favourites, but exposed neither exact `view_count` nor upload chronology.

## 4.22.3 changes

- Probe bootstrap now uses `/catalog`, matching the normal Vinted browsing entry and recording only cookie names (never values).
- Detail acquisition is fail-closed and ordered:
  1. web-session `/api/v2/items/{id}`;
  2. web-session `/api/v2/items/{id}/details`;
  3. read-only public OAuth token (`client_id=ios`, `scope=public`) and the mobile item endpoints.
- OAuth token values are never written to logs or reports.
- Public HTML item pages are no longer used as an exact-view fallback. This removes a possible self-view contamination path.
- When no explicit probe categories are configured, the live probe now uses catalog IDs `4,5` instead of the global ALL feed. The goal is to benchmark the same category-scoped shape that production Vinted Radar will use.
- Exact views, chronology, identity and stability still remain hard Radar gates; missing data stays UNKNOWN.
- Session telemetry reports cookie names plus whether `access_token_web`/`refresh_token_web` were established and whether public OAuth succeeded.
- No DB migration and no new required Railway variables.
- Kleinanzeigen Parser/Page/Date/View/Radar code paths are unchanged by the Vinted probe behavior.

## Railway

Redeploy only the isolated `Vinted Probe` service. No new variable is required for this test. Existing optional `VINTED_ACCESS_TOKEN_WEB` remains supported but is not required.

## Expected next log

The important lines are:

- `session={... public_oauth: ...}`
- per-category unique-depth recovery for catalog `4` and `5`
- `exact_views`
- `chronology`
- `view_stability`

If the public OAuth/mobile route also cannot expose exact views, the probe will continue to fail closed and the next step will be an explicitly authenticated web-session test rather than guessing or treating catalog zeroes as real views.
