# DT Parser v4.3.18 — Dual View Worker

## Railway

Use the existing **View Worker** service and scale it to **2 replicas**. Both replicas use the same `REDIS_URL`. No extra variables are required for the default tuning.

Default per replica:

```env
VIEW_WORKER_POOL_MIN=6
VIEW_WORKER_POOL_DEFAULT=8
VIEW_WORKER_POOL_MAX=10
VIEW_WORKER_BROWSER_POOL_SIZE=1
VIEW_WORKER_ROUND_SIZE=32
VIEW_WORKER_MAX_ACTIVE_JOBS=2
```

You do not need to add these manually unless you want to override the defaults.

The main bot keeps `REMOTE_VIEW_WORKER_ENABLED=1` and the same `REDIS_URL`.

The parser/view extraction core is unchanged.
