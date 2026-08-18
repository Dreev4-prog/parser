# v4.3.11 — Stable 4 Users

Stability rollback for the v4.3.9/v4.3.10 category pipeline experiment.

- Up to 4 user scan jobs remain supported (`MULTIUSER_LOCAL_WORKERS=4`).
- Categories are sequential inside each user job (`CATEGORY_PIPELINE_PER_JOB=1`).
- Old Railway `CATEGORY_PIPELINE_PER_JOB=2` is ignored by code.
- Process-wide category cap is 4.
- Exact public-view phases remain capped at 2 globally.
- v4.3.10 official-counter / 403-429 / Chromium fallback safeguards are preserved.

Recommended Railway:

```text
MULTIUSER_LOCAL_WORKERS=4
CATEGORY_PIPELINE_GLOBAL_LIMIT=4
INLINE_VIEW_PHASE_GLOBAL_LIMIT=2
MULTIUSER_VIEW_POOL_SIZE=9
MULTIUSER_VIEW_MIN_INTERVAL_SECONDS=0.05
```

Remove `CATEGORY_PIPELINE_PER_JOB=2` if it exists. The code still forces 1, but removing it avoids confusion.
