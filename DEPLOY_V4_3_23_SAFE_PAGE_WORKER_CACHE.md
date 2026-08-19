# v4.3.23 — Safe Page Worker Cache

Fixes a v4.3.22 regression where a weak/challenge Page Worker response could be cached and replayed by stable retries, leaving a category partial.

- Page Worker caches only structurally verified pages.
- Main bot applies the stable target-date quality gate to every remote cached page.
- Weak remote pages are deleted from Redis and fetched by the original local stable parser.
- Streaming, 180s cache, 2 Page Worker replicas and View Sharding remain enabled.
- parser.py and traffic.py are unchanged.
