# Kleinanzeigen Parser v1.1

Category parser foundation for public Kleinanzeigen pages.

Collects:
- category
- title
- price
- listing URL
- external listing ID
- view-count snapshots when the public page exposes them
- first/last seen timestamps

## What changed in v1.1

v1.0 looked only for visible `Aufrufe/Besucher` text in the static HTML.

v1.1 tries, in order:
1. public static HTML and embedded state/JSON;
2. a real headless Chromium page with JavaScript enabled;
3. JSON responses requested by that public page.

It does **not** bypass login, CAPTCHA, bot protection, or other access controls. If Kleinanzeigen does not expose a view count publicly, the result remains `views=None` and the log says `source=...not-exposed`.

## Railway

This version includes a Dockerfile based on the official Playwright Python image so Chromium and its Linux dependencies are available on Railway.

Keep your current Railway Custom Start Command, for example:

```bash
python main.py --category "Konsolen" --url "https://www.kleinanzeigen.de/s-konsolen/c279" --max-items 20
```

After uploading v1.1 to GitHub, Railway should automatically rebuild using the Dockerfile.

## Environment variables

- `VIEW_MODE=auto` — first HTTP, then Chromium fallback (recommended)
- `VIEW_MODE=http` — HTTP only
- `VIEW_MODE=browser` — Chromium only
- `REQUEST_DELAY_SECONDS=2.0` — delay before opening each listing
- `BROWSER_WAIT_MS=2500` — wait after DOM load for JS/network data
- `BROWSER_TIMEOUT_MS=20000` — page navigation timeout

Use conservative request intervals and respect the site's rules and access controls.
