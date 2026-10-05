# DT Parser 4.23.23 — complete Radar analytics on the first screen

The administrator's main `DT Radar 3.0` button and `/radar` command now open the
same complete persisted Adaptive Analytics screen as the dedicated admin entry.
The screen includes:

- the active Radar funnel and due measurements;
- Candidate, Early/Score, Strong interval and confirmed HOT counters;
- persistence, acceleration, confidence and observed growth;
- live-demand category statistics;
- checkpoint queue and 24-hour baseline coverage;
- Fast Sold availability checks.

The main parser guarantees an initial complete snapshot before it starts
Telegram polling. If a saved snapshot already exists, startup performs only a
primary-key lookup. If it is absent, the parser calculates and persists it once,
so the first user click does not launch heavy analytics.

Normal screen reads no longer run the lightweight product query in parallel
with the saved snapshot. The product query remains only as an emergency fallback
if no complete snapshot can be read.

The dedicated Radar Worker remains supported and refreshes the same snapshot in
the background. No database migration is required, no Radar data is deleted,
and scoring/admission rules are unchanged.
