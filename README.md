# Kleinanzeigen Parser Bot v3.0.5

## Date Detection Fix + Date Start + 25 / 50 / 100 pages

v3.0.5 keeps the simple scan flow from v3.0.4:

1. choose categories;
2. enter the Moscow calendar date;
3. choose **25 / 50 / 100 pages**;
4. the bot quickly locates the first page of that date;
5. only then does the requested page depth begin.

The important change is that the date locator now validates what Kleinanzeigen actually returned instead of trusting the requested page number.

### Fixed in v3.0.5

- Publication date is read from the listing's dedicated top-right metadata block first (`.aditem-main--top--right`). A date written inside a product title no longer becomes the publication date.
- Page classification uses the distribution/median of dated organic listings. One stray old/new card cannot move the binary-search boundary by itself.
- The parser reads the result range such as `26 - 50 von 469.976` and derives the real maximum page dynamically.
- If a too-large page number is normalized/redirected by Kleinanzeigen, that response is marked invalid instead of being treated as the requested page.
- Repeated page-content fingerprints are detected as an additional safety check.
- Logs now include the date distribution and actual result range for every sparse probe, making future date issues much easier to diagnose.
- User warnings include the concrete failure reason rather than only `самая старая дата`.

## How the scan depth works

The selected date is the starting point. **25 / 50 / 100** is still the number of pages collected from that date.

Example: if `10.08.2026` starts around Kleinanzeigen page 620 and the user selects 50 pages, the locator reaches that area with sparse jumps and binary search. The real collection window then starts at that boundary and can use up to 50 pages. If the selected date ends earlier, the scan stops when the feed moves to the previous calendar date.

Listings encountered only while locating the date are not saved and do not receive view-counter requests.

## Fast Date Jump

The bot does not walk pages 1 → 2 → 3 → ... to reach an older date. It probes approximately:

`1 → 2 → 4 → 8 → 16 → ...`

and then binary-searches the boundary.

`DATE_JUMP_MAX_PAGE` remains only a hard emergency guard. v3.0.5 normally lowers that bound automatically from Kleinanzeigen's current result count, so an impossible page such as 20,000 is no longer silently interpreted as a real page when the category has fewer pages.

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
