# Railway — DT PARSER v4.1.4

Required services:

- `parser` — Start Command: `python bot.py`
- `fleet-worker` — Start Command: `python fleet_worker.py` (start with 1 replica; after validation scale to 6)
- `views-worker` — Start Command: `python views_worker.py`
- Redis
- PostgreSQL

All three Python services must reference the same `REDIS_URL` and `DATABASE_URL`.

The bot now refuses to start a scan when Redis has no live parser/fleet worker heartbeat, so it cannot remain forever on "Подготавливаю скан".

First validate `fleet-worker` with one replica. Its logs must contain `DT PARSER worker online`. Then run one 25-page scan. Only after that scale the fleet to 6 replicas.
