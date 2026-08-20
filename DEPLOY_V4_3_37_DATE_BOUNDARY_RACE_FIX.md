# v4.3.37 — DATE BOUNDARY RACE FIX

Fixes a multi-replica Date Worker race where a deep target page could finish before earlier probes and be treated as the boundary hint.

## What changed

- Wide remote brackets are refined even when a direct target page already exists.
- Foreground verification is capped at 6 linear backward target pages. If a hint is still too deep, the bot falls back to the proven local exponential/binary locator instead of walking dozens of pages one-by-one.
- Exact date validation is unchanged: remote Date Worker results remain hints only.
- Designed for Date Worker x4 / Page Worker x4 / View Worker x4.

No new Railway variables are required. Optional override: `REMOTE_DATE_MAX_LINEAR_WALKBACK=6`.
