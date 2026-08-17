# DT PARSER v4.2.3 — Official View Counter Fix

This is a surgical fix on top of v4.2.2. The scan/date/browser architecture is unchanged.

## Fixed

Accurate Views now accepts Kleinanzeigen's endpoint-specific public counter fields `numVisits` and `numVisitsStr` from `/s-vac-inc-get.json`. Generic `num`, `count`, `value`, impressions and unrelated integers remain rejected.

If both official fields are present, their numeric values must agree. If they conflict, the view count remains unknown rather than guessing.

The rendered DOM remains authoritative when available; the page's own public counter XHR is the verified fallback.
