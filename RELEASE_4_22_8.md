# DT PARSER v4.22.8 — Vinted Local Session Helper

This release replaces the v4.22.7 Railway-hosted Vinted login browser with a local-browser pairing flow.

## Why

Vinted may reject interactive login from a Railway/datacenter IP as VPN/proxy traffic. v4.22.8 no longer asks the administrator to enter Vinted credentials into a browser running on Railway.

## New flow

`Admin -> Vinted Lab -> Vinted Session -> Войти через мой Chrome`

1. Session Worker creates the same one-time 15-minute ticket.
2. The setup page opens Vinted in the administrator's normal Chrome on the administrator's own internet connection.
3. A small Chrome MV3 helper, installed once, waits for `/api/v2/users/current` to confirm a real logged-in Vinted user.
4. Only first-party Vinted cookies + browser metadata are returned over HTTPS to the administrator's own Session Worker.
5. PostgreSQL stores the session; Metrics Workers hot-reload it in about 10–15 seconds.

## Security boundaries

- password and 2FA are entered only on Vinted itself;
- the helper does not read password fields and never receives password/2FA values;
- the one-time ticket remains in URL fragments, not normal request URLs;
- only `vinted.de` cookies are accepted by the server;
- the extension requests host permission only for `vinted.de`;
- cookie payload is not persisted in extension storage;
- no CAPTCHA solving, proxy rotation, stealth/fingerprint bypass, or challenge bypass is implemented.

## Railway

Keep the existing **Vinted Session Worker** service and its public domain on port 8080. No new Railway service and no new environment variable are required.

## Important

This change fixes the interactive login path. It does not guarantee that Vinted will accept every later Metrics Worker request from a datacenter IP; protected responses remain fail-closed UNKNOWN.

Kleinanzeigen Parser/Page/Date/View/Radar/AutoScan and Vinted scan/radar algorithms are unchanged.
