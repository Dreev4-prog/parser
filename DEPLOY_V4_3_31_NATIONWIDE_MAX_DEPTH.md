# v4.3.31 — NATIONWIDE MAX DEPTH

## Goal

Remove the second multi-minute regional date-search phase from normal scans.

## New depth semantics

`15 / 25 / 50` is a maximum number of verified pages for the selected date in the Germany-wide feed.

Examples:

- selected 50, date has 9 nationwide pages -> collect 9 and finish;
- selected 25, date has 40 nationwide pages -> collect 25 and finish;
- selected 50, date starts at page 38 and continues beyond page 50 -> collect the verified target-date pages from 38..50 and finish;
- target date itself is deeper than the nationwide public window -> report the public-window limitation and do not start regional hidden-fill.

## Accuracy

The page/date verification rules are unchanged. Weak/invalid pages are still retried and a genuine page identity failure still makes the category partial. This release only changes what happens when the nationwide public window is exhausted.

## Railway

No new variable is required. Regional hidden-fill is OFF by default.

Optional rollback:

```env
REGIONAL_HIDDEN_FILL_ENABLED=1
```

With the flag set to `1`, the old regional fill path is available again.
