# DT PARSER v4.3.26 — FAST DATE PREDICTOR

This release adds a conservative learned Date Predictor on top of v4.3.25 HTTP-FIRST Date Worker.

## What changes

- A locally confirmed `category/feed + target date -> boundary page` is remembered in Redis for 60 minutes by default.
- The next lookup for the same date probes a tight window around the remembered boundary first.
- After at least two confirmed dates are known for the same category/feed, the manager can interpolate or extrapolate a starting page for another date in the seven-day product window.
- Predictor values are **hints only**. They never become final date truth.
- If the learned window no longer brackets the target date, the existing v4.3.25 parallel exponential probes run unchanged.
- The main bot still locally revalidates the Date Worker result before collection.

## Accuracy safety

Predictor data is written only after the foreground stable parser has locally confirmed the target date. Raw Date Worker guesses are never learned directly.

The predictor rejects contradictory learned chronology. A newer day must trend toward a smaller page number; otherwise the learned estimate is ignored and the normal Date Worker locator is used.

## Default settings

No new Railway Variables are required.

Optional tuning:

```text
DATE_PREDICTOR_TTL_SECONDS=3600
DATE_PREDICTOR_EXACT_RADIUS=3
DATE_PREDICTOR_ESTIMATE_RADIUS=6
```

Recommended: keep defaults while validating production timings.

## Expected behavior

Cold category/date:

```text
HTTP-first Date Worker -> parallel 1/2/4/8/16/32/50 probes -> local verify
```

Warm exact date:

```text
confirmed hint ~ page 30 -> probe around 27/29/30/31/33 -> local verify
```

Learned category with two neighbouring dates:

```text
14 Aug -> page 32
15 Aug -> page 26
13 Aug -> predictor estimates page 38 -> tight probes -> local verify
```

If the estimate has drifted too far, the system automatically falls back to the cold locator.

## Railway

Keep the existing services and replicas:

- Date Worker x2
- Page Worker x2
- View Worker x2
- Redis
- PostgreSQL
- Main Bot

No service or Start Command changes are required.
