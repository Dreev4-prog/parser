# DT PARSER v4.3.26 — FAST DATE PREDICTOR

v4.3.26 adds a learned date-boundary predictor on top of the conservative HTTP-first Date Worker from v4.3.25.

```text
FAST DATE PREDICTOR
        ↓
Date Worker x2 (HTTP-first -> browser confirm when weak)
        ↓
Page Worker x2
        ↓
View Worker x2
```

Key rules:

- only **locally confirmed** date boundaries are learned;
- exact predictor hints live 60 minutes by default;
- after two confirmed dates, the manager may interpolate/extrapolate another date's likely page;
- learned pages are only starting points, never final answers;
- bad/stale predictions automatically fall back to the v4.3.25 parallel exponential locator;
- final date boundary is still locally verified by the stable parser;
- date selection remains limited to today + previous six days;
- Page Worker, View Worker, View Sharding, parser.py and traffic.py are unchanged.

No new Railway variables are required. See `DEPLOY_V4_3_26_FAST_DATE_PREDICTOR.md`.
