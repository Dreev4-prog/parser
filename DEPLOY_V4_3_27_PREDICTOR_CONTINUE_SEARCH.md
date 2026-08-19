# DT PARSER v4.3.27 — PREDICTOR CONTINUE SEARCH

## Deploy

Upload the full ZIP to the same GitHub repository and redeploy the existing Railway services.

No new services and no new mandatory environment variables are required.

Keep the current architecture:

- Main Bot
- Date Worker ×2
- Page Worker ×2
- View Worker ×2
- Redis
- PostgreSQL

## New date behaviour

When Predictor says, for example, `14.08 ≈ page 30`:

1. Date Manager first checks the tight predictor window around page 30.
2. If the date boundary has moved, it continues outward from page 30 (for example page 36, then 42...) instead of restarting at pages 1/2/4/8/16/32/50.
3. If chronology clearly shows the target is only deeper or only shallower, expansion follows that direction only.
4. The bracket is refined before it is returned to the main bot.
5. The main bot still performs the same stable local verification before accepting the date.

The old exponential locator remains only as an emergency fallback for cold/unusable predictor evidence.

## Admin check

Open `📅 Date Worker` after a repeated date scan. Useful fields:

- `Continue search: success/total`
- `Продолжение от hint: rounds`
- `новых pages`
- `полный fallback`

For a remembered date whose boundary merely shifted, expected result is `полный fallback: нет`.
