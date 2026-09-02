# DT Radar 3.2 — Live / History Split (4.21.13)

- The 6-hour observation TTL now changes a stale Radar product to `historical` without zeroing its confirmed Score.
- `radar_rank` becomes zero for History so stale products cannot compete in live ranking.
- One-time startup repair restores historical rows that 4.21.12 had already zeroed, using `last_signal_score` with `peak_score` fallback.
- Hot, Rising, category New/Best, generic live catalogue, category counters and Search exclude `historical` products.
- `Records Radar` remains the explicit historical view and sorts by Peak Score.
- Favorites may keep historical products because they were explicitly saved by the user.
- Historical product cards display the last confirmed Score and Peak Score instead of pretending the signal is live.
- No Radar reset is performed in 4.21.13; existing evidence is preserved and repaired.
