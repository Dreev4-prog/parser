# DT PARSER v4.22.8 — GitHub patch from v4.22.7

Overlay this patch on top of **v4.22.7**. Do not delete unrelated repository files.

## Changed

- `bot.py` — local Chrome wording/buttons only.
- `vinted_session_worker.py` — Railway remote browser removed; Session Worker now validates one-time tickets, serves the Local Helper download, and accepts a bounded first-party session over HTTPS.
- `vinted_local_helper/` — Chrome MV3 helper installed once on the admin computer.
- `VERSION` -> 4.22.8.
- release/tests.

## Railway

No new service. Keep the existing `Vinted Session Worker`:

- Start Command: `python service_launcher.py`
- Replicas: 1
- `DATABASE_URL`: same DB
- Public Networking domain -> port `8080`

Redeploy **Parser** and **Vinted Session Worker** after overlaying the patch. Metrics Workers may stay on the same code if they already contain the v4.22.7 DB hot-reload path, but deploying all Vinted services from the same commit is recommended for version consistency.

## One-time local setup

Open `Admin -> Vinted Lab -> Vinted Session -> Войти через мой Chrome`.
The setup page contains `Скачать DT Vinted Local Helper` and installation instructions for Chrome.
After the helper is loaded once, later session refreshes are just: open the one-time login -> log in to Vinted -> automatic save.
