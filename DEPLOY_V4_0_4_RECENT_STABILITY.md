# DT PARSER v4.0.4 — Recent Stream Stability

This release fixes the root cause of repeated partial scans on today / yesterday / day-before-yesterday.

## What changed

- Stable Engine collection now uses the real per-page `stable_fetch()` retry wrapper.
- A weak page is retried in-place instead of immediately poisoning the whole category.
- Recent-date stream pages with sparse timestamps can count toward requested depth once the target-day window has been entered. Only listings with an exact parsed target date are inserted.
- Once a recent target day has been observed, reaching the public nationwide window no longer launches the expensive regional hidden-fill merely because a few timestamp templates were weak.
- Recent scans start directly in the collecting UI phase.
- Existing Browser Fleet architecture is unchanged.

## Railway

Keep:
- bot ×1
- fleet-worker ×6
- views-worker ×1
- Redis ×1
- PostgreSQL ×1

Fleet worker start command:

```bash
python fleet_worker.py
```

No PostgreSQL migration is required.

Recommended smoke test after deploy:
1. today, 1 category, 25 pages, no minimum-view threshold;
2. yesterday, same category;
3. then 5 accounts simultaneously.
