# Kleinanzeigen Parser Bot v3.0.4

## Date Start + 25 / 50 / 100 pages

v3.0.4 restores the simple scan flow:

1. choose categories;
2. enter the Moscow calendar date;
3. choose **25 / 50 / 100 pages**;
4. the bot quickly locates the first page of that date;
5. only then does the requested page depth begin.

Example: if `10.08.2026` starts around Kleinanzeigen page 1730 and the user selects 50 pages, the locator reaches that area with sparse jumps and binary search, then the real collection window is approximately pages 1730–1779. Listings from newer dates encountered during locating are not stored and do not receive view-counter requests.

If the selected date ends before all requested pages are used, the scan stops when the feed moves to the previous calendar date. Results contain only listings whose normalized publication date matches the selected Moscow date.

## Fast Date Jump

The bot does not walk pages 1 → 2 → 3 → ... to reach an older date. It probes approximately:

`1 → 2 → 4 → 8 → 16 → ...`

and then binary-searches the boundary. `DATE_JUMP_MAX_PAGE` is only a high technical guard for the sparse locator. It is not the user's scan depth.

Default: `DATE_JUMP_MAX_PAGE=20000` (optional; no Railway change is required).

## Exact saved scan snapshots

Saved scans now keep the exact listing IDs collected by that 25/50/100-page run. A 25-page scan will therefore not silently turn into a larger result just because the database already contains more listings from the same date.

Simultaneous/cache reuse is keyed by **category + date + depth**, so a 25-page and a 100-page request are not confused with one another.

## Preserved features

- fast direct public view counter with browser fallback
- promoted/Top/Highlight listing filter
- Moscow-time publication-date normalization
- multi-user queue and shared identical scans
- saved `Мои сканы`
- manual view refresh and 1/3/6/24h view velocity
- product identity recognition and model grouping
- CSV identity columns and Smart Analytics modes

## Railway

No required setting changes.

Start command:

```bash
python bot.py
```
