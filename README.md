# DT PARSER v4.3.25 — HTTP-FIRST DATE WORKER

This release accelerates Date Worker probes with a conservative HTTP-first path.

```text
Date Worker x2 (HTTP-first -> browser confirm when weak)
        -> Page Worker x2
        -> View Worker x2
```

Accuracy rules:

- HTTP never decides the final date boundary.
- Only high-confidence HTTP chronology probes are cached directly.
- Empty, mixed, low-date-evidence and single-target HTTP probes are browser-confirmed.
- Explicit 403/429 is never bypassed with a different transport.
- The main stable parser still locally verifies the Date Worker boundary before collection.
- Any worker failure/inconsistency falls back to the existing stable local date locator.

Date selection remains limited to today + previous 6 days. Page Worker, View Worker, View Sharding and the parser/traffic cores are unchanged from v4.3.24.

See `DEPLOY_V4_3_25_HTTP_FIRST_DATE_WORKER.md`.
