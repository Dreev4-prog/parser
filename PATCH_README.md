# DT PARSER v4.22.7 — GitHub Web patch from v4.22.6

Replace/add these files on top of **v4.22.6**. Do not delete any other repository files.

## What this patch adds

Admin-only login flow:

`Админ-панель -> Vinted Lab -> Vinted Session -> Открыть вход Vinted`

A new isolated Railway service opens the Vinted browser session. The admin logs in manually in the secure one-time browser page and presses `Сохранить сессию`. The resulting browser cookies are saved in PostgreSQL; password/2FA text is not persisted or logged.

## Railway: one new service

Create from the same repository:

- **Name:** `Vinted Session Worker`
- **Start Command:** `python service_launcher.py`
- **Replicas:** 1
- **DATABASE_URL:** same PostgreSQL as Parser/Vinted workers
- **REDIS_URL:** not required
- **BOT_TOKEN:** not required
- **VINTED_SESSION_JSON:** not required

Then open **Networking -> Generate Domain** for this service. If the service was already running, redeploy once after the domain is generated.

The Session Worker publishes its HTTPS address to PostgreSQL. Parser reads it automatically; no URL variable is needed.

## Existing services

Deploy Parser and both Vinted Metrics Worker replicas from v4.22.7. Scan Worker can stay on the same checkout/version for consistency; its algorithm is unchanged.

Metrics Worker priority for session source:
1. explicit legacy `VINTED_SESSION_JSON` env, if present;
2. otherwise session captured from Admin and stored in PostgreSQL.

If you want Admin login to control the session, do not keep an old `VINTED_SESSION_JSON` override on Metrics Worker.

After a new Admin session is saved, Metrics Worker hot-reloads it automatically in about 10–15 seconds; no redeploy is needed.

## Security boundary

- login URL uses a one-time 15-minute token;
- token is kept in the URL fragment and sent to the service in a header, so it is not placed in normal HTTP access-log URLs;
- aiohttp access logging is disabled on the Session Worker;
- login text/password/2FA are forwarded only to the isolated browser keyboard and are never stored/logged;
- only first-party Vinted browser session state is stored;
- no CAPTCHA solving, proxy rotation, stealth/fingerprint bypass, or challenge bypass is included.

Kleinanzeigen parser/Page/Date/View/Radar/AutoScan v4.22.6 logic is unchanged.
