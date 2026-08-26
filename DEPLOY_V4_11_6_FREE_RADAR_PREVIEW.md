# DT PARSER v4.11.6 — Free Radar Preview

Base: v4.11.5 AutoScan Stability.

## Goal

Give non-subscribers a real, useful DT Radar demo instead of a completely locked screen. The preview must demonstrate live product value without exposing the full accumulated database.

## Free user flow

A user without an active subscription can now open:

`DT Radar -> Лучшие сейчас`

They may choose:
- `Горячие`
- `Набирают`
- `AI Picks`

Each mode shows only the first **5 real current Radar products**. The preview includes the same DT Score, freshness, category and product detail card used by the paid product. The current Kleinanzeigen listing can be opened from the preview product card when an active listing URL exists.

After the five visible products, the bot shows how many additional products are hidden and offers `Открыть полный DT Radar`.

## Locked in free mode

The following remain subscription-only:
- all results after the first 5 in each Best Now mode
- pagination
- Radar Search
- Categories and subcategories
- My Radar / favorites
- Radar Records

Locked buttons remain visible on the Radar home with a lock marker so users understand what the paid product contains.

## Free scans remain separate

The existing two-scan free trial is not changed. If the user still has a trial scan remaining, the free Radar result screen also shows the existing `Бесплатный скан` action. Radar preview does not consume scan credits.

## Access behavior

- active subscriber/admin: full Radar unchanged
- non-subscriber in subscription mode: read-only 5-product preview
- banned user: no preview
- admin-only mode: no public preview
- open mode: full access follows the existing access policy

Free preview product callbacks are isolated from paid `radaritem` callbacks. Search/category/favorite callbacks are not exposed by the free keyboard.

## Preserved behavior

Unchanged from v4.11.5:
- 84-category product-only AutoScan
- one warm parser session per AutoScan round
- AutoScan cooldown/retry/error classification
- exact official view-counter path and bounded browser fallback
- Radar Category Feed and DT Score
- Page/Date/View/AI workers
- four user parser lanes and FIFO queue
- subscription plans and payment providers
- PostgreSQL schema

## Railway

No new variables and no DB migration.

Redeploy only **parser**. Dedicated Date/Page/View/AI worker entrypoints are unchanged.

## Smoke test

1. Deploy parser and confirm startup reports v4.11.6.
2. Open the bot from an account without an active subscription.
3. Confirm the main menu shows `DT Radar` instead of a fully locked Radar button.
4. Open `DT Radar -> Лучшие сейчас -> Горячие` and confirm exactly up to 5 real products are shown.
5. Open one preview product and confirm DT Score/freshness/detail + current Kleinanzeigen link are visible.
6. Confirm the preview product card has no `Добавить в Мой Radar` action.
7. Confirm page 2, Search, Categories, My Radar and Records lead to the full-access upsell.
8. If trial credits remain, confirm the free-scan button is present and Radar preview does not decrement the credit.
9. Repeat from an active paid account and confirm full Radar remains unchanged.
