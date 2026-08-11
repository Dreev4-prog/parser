# Kleinanzeigen Parser v1

Minimal foundation for collecting listings from a public Kleinanzeigen category page.

Stores:
- category
- title
- price
- listing URL
- external listing ID
- view count snapshots
- first/last seen timestamps

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

## Run

Copy a public Kleinanzeigen category URL from your browser, then:

```bash
python main.py \
  --category "Elektronik" \
  --url "PASTE_PUBLIC_CATEGORY_URL_HERE" \
  --max-items 20
```

The first version uses SQLite so it is easy to test locally. The model is already compatible with migrating to PostgreSQL later by changing DATABASE_URL and installing asyncpg.

## Database structure

`listings` keeps the current ad data.

`view_snapshots` stores every measured view count. This lets later versions calculate +views in 24h / 3 days without changing the schema.

## Notes

The HTML selectors are intentionally isolated in `parser.py`. If Kleinanzeigen changes markup, only this file should need adjustment.

Use a conservative request interval and respect the site's rules and access controls. This project does not include CAPTCHA bypassing, login automation, proxy rotation, or anti-bot evasion.
