# DT PARSER v4.11.4 — Radar Category Feed

Base: v4.11.3 DT Radar Simple Home.

## Goal

Keep the new simple category navigation, but make each category a permanent curated Radar catalogue instead of showing only the last 24 hours. New products must still be obvious and appear first.

## User flow

`DT Radar -> Категории -> large section -> subcategory`

The leaf-category button now shows:

`📂 Konsolen · 184 · 🆕 17`

- `184` = all products in this leaf category that entered DT Radar
- `🆕 17` = products whose `first_radar_at` is today in Moscow time

The large-section button keeps one uncluttered total count.

## Category product feed

The 24-hour category filter from v4.11.3 is removed. All accepted Radar products remain visible.

Default order:
1. newest `first_radar_at`
2. DT Score
3. latest signal time

This means a historical product receiving another measurement does not masquerade as a newly discovered product.

Freshness labels in category feeds:
- under 3 hours: `🆕 Новое · N мин/ч назад`
- same Moscow calendar day: `🟢 Сегодня`
- previous day: `вчера`
- within a week: `N дн назад`
- older: calendar date

One toggle is available:
- default feed: `🔥 Сначала лучшие`
- best feed: `🆕 Сначала новые`

`Сначала лучшие` sorts the same accumulated category by current DT Score, then freshness.

## Unchanged

- DT Score / Peak Score calculations
- Simple Radar home and Search
- Hot / Rising / AI Picks / Records
- Radar AutoScan 141 leaf categories / 15 pages
- AutoScan Error Recovery
- Page Cache Recovery
- Date/Page/View/AI workers
- four user parser lanes
- database schema and subscriptions

## Railway

No new Railway Variables and no database migration. Redeploy only the main `parser` service.

## Smoke test

1. Open `DT Radar -> Категории`; verify large sections show accumulated totals.
2. Open a large section such as `Elektronik`; verify leaf buttons show total counts and `🆕 today` where applicable.
3. Open a subcategory; verify results are not limited to the last 24 hours.
4. Verify recently added products appear first and show `🆕 Новое` or `🟢 Сегодня`.
5. Press `🔥 Сначала лучшие`; verify the same category is reordered by DT Score.
6. Press `🆕 Сначала новые`; verify newest ordering returns.
7. Verify pagination preserves the selected ordering.
8. Verify Admin -> Radar AutoScan and retry-errors still open normally.
