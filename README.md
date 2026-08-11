# Kleinanzeigen Parser Bot v2.6.8 — Robust public view counter diagnostic

This release keeps v2.6.7 functionality and makes public-view extraction more tolerant of Kleinanzeigen markup variants.

## What changed

- Keeps the legacy `#viewad-cntr-num` selector as the preferred source.
- Adds fallback extraction from the public `#viewad-extra-info` block (date/time + eye counter).
- Checks alternate `data-testid`, id/class names related to view/counter/Aufrufe.
- Checks embedded page hydration JSON for `views`, `viewCount`, `view_count`, or `impressions`.
- The **👁 Тест просмотров** diagnostic now reports the final URL, page title, extra-info text, and whether Railway appears to receive a normal ad page, a redirect, cookie-only page, or a challenge/protection page.
- No authentication, CAPTCHA bypass, or protection bypass is implemented.

## Deploy

Replace the files in the same GitHub repository and redeploy Railway.

Start command remains:

```bash
python bot.py
```

Required environment variables remain unchanged:

```env
BOT_TOKEN=...
ADMIN_IDS=123456789
```
