# DT PARSER v4.22.7 — Admin Vinted Session Login

Base: v4.22.6 AutoScan Fast Today + Exact Tail.

This release removes the local Mac/Windows session-capture requirement. Vinted authentication can now be captured from the Telegram admin flow through an isolated Railway service.

## New isolated service

Create one Railway service from the same checkout:

- name: `Vinted Session Worker`
- start command: `python service_launcher.py`
- replicas: 1
- attach the same `DATABASE_URL`
- in Railway Networking, click **Generate Domain** once

No BOT_TOKEN, REDIS_URL or VINTED_SESSION_JSON is required on this service.

## Admin flow

`Admin -> Vinted Lab -> Vinted Session -> Open Vinted login`

The bot issues a single-use 15-minute login URL. The Session Worker opens a normal Playwright browser context and streams only screenshots/control events to the admin page. The admin logs in manually and presses `Save session`.

- passwords and 2FA values are never written to logs or PostgreSQL;
- only the resulting first-party Vinted browser cookies/storage state are saved;
- no CAPTCHA solving, stealth bypass, fingerprint spoofing or proxy rotation is implemented;
- a challenge must be completed manually by the admin or the session remains unavailable.

## Session storage and hot reload

The resulting session is stored in PostgreSQL `app_settings`. Vinted Metrics Worker replicas now use:

1. `VINTED_SESSION_JSON` if explicitly configured (legacy/manual override);
2. otherwise the admin-captured PostgreSQL session.

When the admin saves a newer DB session, Metrics Worker checks every 10 seconds and hot-reloads its isolated Vinted browser context. A Railway redeploy is not required.

The Parser also reuses the DB session for Vinted category metadata if anonymous catalog metadata is incomplete.

## Isolation

Kleinanzeigen parser, Page Worker, Date Worker, View Worker, Radar 3.2 score, Organic Gate, Lifecycle and AutoScan v4.22.6 behavior are unchanged.
