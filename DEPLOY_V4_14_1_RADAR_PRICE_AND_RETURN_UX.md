# DT PARSER v4.14.1 — Radar Price & Return UX

**Base:** v4.14.0 Fast Sold Lifecycle.

This release changes only DT Radar browsing/search UX. Parser, AutoScan, Date/Page/View/AI/Lifecycle algorithms remain unchanged.

## Added

### 1. Radar price filter
Paid DT Radar users can filter accumulated Radar products by an actually observed listing price.

Available presets:
- any price;
- up to 50 €;
- 50–100 €;
- 100–200 €;
- 200–500 €;
- 500+ €;
- custom range such as `120-250`, `до 100`, or `500+`.

The filter is available in:
- category product feeds;
- Radar text search results.

The selected filter persists while the user continues browsing Radar and is reset by choosing `Любая`.

The DB filter uses `radar_product_listings.last_price_eur`, so a product family is included only when Radar has actually observed at least one listing inside the requested price range. It does not rely only on the broad family min/max envelope.

### 2. Return to the exact Radar list
Opening a Radar product now keeps the current browsing context.

For category browsing the product card shows:
- `⬅️ Назад к категории`

It returns to the same:
- category;
- page;
- sorting mode (`newest` / `best DT Score`);
- active price filter.

Search results return with `⬅️ К результатам`, and ordinary Hot/Rising/AI/Fast Sold/Records/Favorites lists return with `⬅️ К списку`.

Adding/removing a product from `⭐ Мой Radar` no longer destroys this return context.

### 3. Price visibility
Radar category/search result text now shows the observed product price range so the active filter is understandable without opening every product.

## Compatibility
- No PostgreSQL migration.
- No new Railway variables.
- No new worker service.
- `Lifecycle Worker` from v4.14.0 remains fully compatible.
- DT Score / AI Lab formula is intentionally unchanged in this release.

## Deployment
Redeploy the **parser** service with v4.14.1.

If Railway automatically redeploys all services from the same repository, that is safe; auxiliary workers have no behavioral changes in this release.

## Smoke test
1. Open `DT Radar -> Категории -> any subcategory`.
2. Set `Цена -> 100–200 €` and confirm the result count/list changes.
3. Open a product from page 2+, then press `⬅️ Назад к категории`; verify the same page, sort and price filter are restored.
4. Toggle `⭐ Мой Radar` inside the product and verify the same back button remains.
5. Run `DT Radar -> Поиск`, search e.g. `PlayStation`, set a price filter, open a product and return with `⬅️ К результатам`.
6. Set a custom range (`120-250`) and verify it is shown in the filter button/text.
