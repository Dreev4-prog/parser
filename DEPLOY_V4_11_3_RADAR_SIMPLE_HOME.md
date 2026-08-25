# DT PARSER v4.11.3 — DT Radar Simple Home

Base: v4.11.2 DT Radar Category Navigator.

## Goal

Make DT Radar understandable from the first screen without removing DT Score or the accumulated historical database.

## New Radar home

The old six equal-weight analytics buttons are replaced by four obvious user actions:

- `🔥 Лучшие сейчас`
- `🔎 Поиск`
- `🗂 Категории`
- `⭐ Мой Radar`

`🏠 Меню` remains as navigation back to the bot.

## Лучшие сейчас

`🔥 Лучшие сейчас` opens a second level with:

- `🔥 Горячие`
- `🚀 Набирают`
- `🧠 AI Picks`

Historical all-time ranking is no longer a primary home action and is shown as the secondary `🏆 Рекорды Radar` entry.

Hot and Rising are now separate feeds: `Набирают` contains only products whose current Radar status is `rising`, instead of duplicating current Hot products.

## Radar search

`🔎 Поиск` accepts a normal product/model phrase (for example `Apple TV` or `PlayStation Portal`) and searches the accumulated Radar database by product title.

Results are ordered by current DT Score and freshness and support pagination. Historical products remain searchable.

## Freshness label

Radar list/search cards now show a human freshness label based on the latest Radar signal:

- `только что`
- `N мин назад`
- `N ч назад`
- `вчера`
- `N дн назад`

The product detail card also shows the same freshness label while retaining the exact latest signal timestamp.

## Category activity counters

The hierarchical category navigator from v4.11.2 is retained, but counts now mean **fresh Radar products active in the last 24 hours**, not the entire historical accumulation.

- main section count = sum of its fresh leaf-category products
- subcategory count = fresh products in that subcategory
- opening a subcategory shows the same current 24-hour feed, ordered by DT Score/freshness

Historical products are not deleted. They remain accessible through Search and Radar Records.

## Unchanged

- DT Score and Peak Score formulas
- Radar product/snapshot/history tables
- AutoScan 141 categories / 15 pages
- AutoScan Error Recovery / retry only failures
- Page Cache Recovery
- Date/Page/View/AI worker behavior
- four foreground parser lanes
- subscription/free-trial logic

## Railway

No new variables and no database migration are required.

Redeploy only the main `parser` service. Date/Page/View/AI workers are unchanged.

## Smoke test

1. Open `DT Radar` and verify only four primary Radar actions are shown.
2. Open `Лучшие сейчас`; verify Hot / Rising / AI Picks plus secondary Records.
3. Open `Набирают`; verify products are current Rising products and contain freshness labels.
4. Open `Поиск`, send `Apple TV` (or another known phrase), and verify paginated results with DT Score + freshness.
5. Open `Категории -> Elektronik`; verify subcategory buttons have current 24h counters.
6. Open a subcategory and verify the list is a fresh 24h feed and still displays DT Score.
7. Open a product and verify exact last-signal time plus human freshness label.
8. Verify Admin -> Radar AutoScan and retry-errors screens still work.
