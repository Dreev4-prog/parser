# Kleinanzeigen Parser Bot v3.0.2


v3.0.1 fixes exact-date scanning and keeps all v3.0 product-recognition/view-history features.

## Exact date = automatic depth

The old 25 / 50 / 100-page choice no longer limits a scan for a concrete Moscow date.

After the user enters a date (for example `10.08.2026`), the bot automatically:

1. starts from the newest category page;
2. skips newer calendar days;
3. begins collecting when the requested Moscow date is reached;
4. keeps paging until the feed is entirely older than that date;
5. stops only after the selected day is fully crossed.

The Telegram progress card shows the current page and the oldest calendar date reached instead of a misleading fixed ETA.

## No more false zero because of shallow depth

If Kleinanzeigen temporarily refuses requests or the high safety cap is reached before the requested date is fully collected, the scan is marked **partial** instead of pretending that `0 listings` is a complete answer. The saved scan card keeps the reason.

A true zero is only treated as complete when the parser actually crosses the requested calendar day (or reaches the physical end of the feed) without finding matching listings.

## Safety cap

`DATE_SCAN_MAX_PAGES=1000` is only a guard against endless scanning if Kleinanzeigen changes its ordering. It is **not** the normal user-visible depth. Increase it in Railway only if a very large category genuinely needs more than 1000 pages to reach the target date.

## Preserved from v3.0

- fast direct public view counter with browser fallback
- promoted/Top/Highlight listing filter
- Moscow-time publication-date normalization
- multi-user queue/shared category cache
- saved `Мои сканы`
- manual view refresh and 1/3/6/24h view velocity
- product identity recognition
- model grouping and CSV identity columns
- Smart Analytics modes

## Railway

No required setting changes. Existing deployments work with the default safety cap.

Start command:

```bash
python bot.py
```


## v3.0.2 — Date Jump Search

Exact-date scans no longer walk every page from page 1. The bot probes pages exponentially, binary-searches the date boundary, then sequentially collects only the selected calendar day. View counters are fetched only for matching listings. Promoted Top/Highlight listings remain excluded before date logic.
