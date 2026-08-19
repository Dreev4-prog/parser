# DT PARSER v4.3.29 — SAFE COLD DATE TURBO

This is a safety patch over v4.3.28.

- Keeps Cold Date Turbo for the first historical-date lookup.
- Uses 7 wide cold probes for the oldest dates instead of 9.
- Restores Date Worker default concurrency to 2 per replica (4 total with x2 replicas).
- Restores the previously stable traffic pacing.
- Disables speculative regional Date prewarm by default because it overlapped Page Worker collection and could provoke transient weak/invalid pages.
- Cancels any optional speculative prewarm tasks when a category finishes.
- Page Worker, View Worker, parser.py and traffic.py are otherwise unchanged.

No new Railway variables are required. Keep Date Worker at 2 replicas.
