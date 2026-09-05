# DT PARSER 4.22.2 — Vinted Probe: Current Detail API + Unique Depth Recovery

**Base:** 4.22.1 Vinted HTML Detail + Pagination Fix.

## What the second live Railway probe proved

- catalog access is healthy and fast enough for continued work;
- the public item page matched identity on 24/24 samples;
- public HTML exposed favourites but **0/24 exact view counts** and **0/24 upload timestamps**;
- reusing `pagination.time` did not freeze `newest_first`: 287 rows produced 183 unique items with heavy page overlap;
- the probe correctly kept missing views as `UNKNOWN` and did not run the stability gate without exact views.

## 4.22.2 changes

1. **Current browser detail endpoint first**
   - requests `GET /api/v2/items/{id}/details?localize=true`;
   - exact item ID must match before any metric is trusted;
   - public Next.js HTML remains a fallback/enrichment path only;
   - missing views/date never become zero.

2. **No false snapshot assumption**
   - `pagination.time` is recorded as telemetry only;
   - each catalog request gets a fresh request time.

3. **Sequential pages inside one category**
   - pages 1→2→3 are no longer raced against each other;
   - separate categories may still run concurrently, matching the future two-Scan-Worker architecture.

4. **Bounded unique-depth recovery**
   - target depth = requested pages × page size;
   - if live-feed shifts create duplicates, the probe continues up to 3 extra pages by default;
   - pagination passes when requested unique depth is recovered, or the category is cleanly exhausted;
   - raw duplicate ratio remains visible as telemetry rather than being treated as an automatic failure.

## Radar gate

Vinted Radar remains disabled until all of these pass on live Railway data: catalog access, recovered pagination depth, exact identity, upload chronology, exact views, and repeated-read stability.

No database migration and no new required Railway variables. The Vinted Probe service stays fully isolated from Kleinanzeigen workers.
