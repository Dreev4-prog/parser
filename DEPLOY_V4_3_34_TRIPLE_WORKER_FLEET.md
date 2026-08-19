# DT PARSER v4.3.34 — Triple Worker Fleet

Built on v4.3.33.

## Railway profile
Set replicas to:

- Date Worker: **3**
- Page Worker: **3**
- View Worker: **3**
- Main Bot: **1**
- Redis: **1**
- PostgreSQL: **1**

No new environment variables are required.

## What changed

- Main Bot default local scan capacity is now **4** (`MULTIUSER_LOCAL_WORKERS=4`).
- Regional Date pipeline default concurrency: **3** (was 2).
- Regional Date look-ahead window: **6** (was 4).
- Per-replica Date/Page/View worker concurrency is **unchanged**. Scaling comes from Railway replicas rather than making each replica more aggressive.
- Worker discovery remains dynamic through Redis heartbeats/consumer groups.
- View sharding already detects the number of healthy View Worker replicas dynamically.

## Safety

The exact parser, traffic controller, Date Worker, Page Worker and View Worker cores are unchanged from v4.3.33. If Kleinanzeigen begins returning 403/429 or partial scans under four simultaneous heavy jobs, reduce View Worker back to 2 first; views are not the bottleneck in the current workload.
