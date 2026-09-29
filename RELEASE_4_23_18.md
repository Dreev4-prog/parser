# DT Parser 4.23.18 — instant DT Radar navigation

This patch removes the 15–20 second wait observed when opening DT Radar 3.0.

- Telegram callbacks are acknowledged before FSM or database work.
- Public Radar counters use a 60-second single-flight process cache.
- A cold service waits at most 1.25 seconds for counters; the menu opens with an
  honest “statistics are updating” line while the shared refresh finishes.
- Stale counters remain usable while one background refresh is running.
- Free-preview funnel analytics are written in the background.
- Five sequential Radar product aggregates are combined into one PostgreSQL
  statement, reducing repeated scans of the same live product set.

Scanning, Radar observations, scoring, HOT/Rising classification, retention and
the stored product history are unchanged.

Deploy `bot.py`, `radar.py` and `VERSION` together. No Railway variable or manual
database command is required.
