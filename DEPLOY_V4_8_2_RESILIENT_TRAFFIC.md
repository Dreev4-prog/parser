# DT PARSER v4.8.2 — Resilient Traffic

Base: v4.8.1 Golden Core. Parser/stable-engine logic is unchanged.

## Refusal behavior for Date/Page/View workers
- Redis shared cooldown is disabled for all three worker fleets.
- 403: no process-wide hard pause. The refused request fails normally; only a bounded local penalty remains.
- 429: short local hard pause, capped at 3 seconds.
- Maximum local penalty: 1 level.
- Recovery: 10 successes and 10 seconds quiet instead of the historical 60/60 profile.
- Distributed/global request limits remain enabled.
- View adaptive pool remains enabled, reduces by one slot on refusal, and can recover after a 3-second growth hold.

This release does not try to bypass refusals. It prevents one refusal from freezing unrelated replicas or putting a whole fleet into long exponential backoff.

## Railway
Remove any manual `DIST_TRAFFIC_SHARED_COOLDOWN` override from Date/Page/View; the worker entrypoints force the correct value. Redeploy all three worker services from the same commit.
