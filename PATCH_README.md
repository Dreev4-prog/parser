# v4.23.23 — full DT Radar 3.0 screen restored

Apply over v4.23.22. Opening `DT Radar 3.0` as an administrator now immediately
shows the complete saved Adaptive Analytics screen: the Radar funnel,
Candidate/Early/Strong/HOT, live-demand categories, checkpoint coverage,
baseline funnel and Fast Sold availability checks.

The parser guarantees that this complete snapshot exists before Telegram starts
accepting clicks. A normal open is one primary-key settings read and no longer
waits for the product-list fallback query.

No database migration is required. See `RELEASE_4_23_23.md`.
