# DT PARSER v4.3.27 — PREDICTOR CONTINUE SEARCH

v4.3.27 fixes the expensive second date-search path seen after v4.3.26.

## What changed

- FAST DATE PREDICTOR remains the first date-search layer.
- If the remembered/estimated boundary moved outside the first predictor window, Date Manager now **continues from the hint** instead of discarding it and restarting from page 1.
- Expansion grows outward from the learned page (`radius → 2x → 4x...`) and follows chronology direction when reliable evidence says the target is only deeper or only shallower.
- Already probed pages are not queued again if the emergency exponential fallback is ever needed.
- Remote chronology brackets are tightened to <=3 pages (or a direct target hit) before foreground verification, reducing the chance of a second local locator pass.
- Final date truth is unchanged: the foreground stable parser still revalidates the Date Worker hint locally before accepting a date.

## Unchanged

- `parser.py` / stable page/date parsing core
- `traffic.py`
- Date Worker HTTP/browser transport (`date_worker.py`)
- Page Worker / Page cache
- View Worker / View Sharding
- Railway service layout

No new Railway variables or services are required.

See `DEPLOY_V4_3_27_PREDICTOR_CONTINUE_SEARCH.md`.
