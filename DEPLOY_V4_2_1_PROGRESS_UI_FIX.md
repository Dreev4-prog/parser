# DT PARSER v4.2.1 — Views Progress + Recovery Card Fix

- View collection now updates the scan card continuously instead of staying at `0/N` until the entire batch finishes.
- Every completed public counter request advances the in-memory progress counter; Telegram rendering remains throttled by the existing ticker.
- Failed view counters still count as checked work and are tracked separately, so progress can reach completion.
- Railway restart recovery first edits the existing scan card. A replacement message is created only when the old message cannot be edited.
- Core category parsing, Stable Reset logic, PostgreSQL schema and request concurrency are unchanged.
