# DT Parser v4.3.32 — SMART HYBRID OLD DATE

Fixes the v4.3.31 false-zero regression for far historical dates.

- Ordinary scans remain nationwide-only: no regional fill just to force 15/25/50 pages.
- If Date Worker proves the selected date is deeper than the public nationwide 50-page window, the bot automatically uses regional shards because that is the only way to reach the date accurately.
- A `too_deep` date is never reported as a successful zero.
- Existing Date/Page/View workers and strict page/date verification remain unchanged.
- No Railway changes are required.

Default: `AUTO_REGIONAL_FALLBACK_TOO_DEEP=1`.
