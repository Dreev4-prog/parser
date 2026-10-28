# DT Parser 4.23.30 — category selection in Radar Records

Records now offers a section-to-leaf-category selector with an All categories
reset. The canonical category catalog supplies the choices without querying
live-feed counts. Selection is stored in a dedicated Records session key and
passed to the existing alltime database query alongside the Records budget.

Pagination and product-card back actions retain both filters. Preset/custom
price selection and category changes reset the page to zero while preserving
the other filter. Resetting the category preserves price; Any price preserves
category. Invalid or unpaid callbacks do not mutate state or query records.

Records-specific headings, Peak Score ordering and historical inclusion remain
in place when a category is selected. Pagination continues to use alltime
callbacks instead of switching to live radarcat feeds. The normal category and
search price filter is separate. Empty-result guidance and all new labels have
English copy. Callback lengths are checked against Telegram's 64-byte limit.

The database already supports category plus observed-listing price predicates
before COUNT/OFFSET/LIMIT, so no SQL/schema/scoring change is required. Real
SQLite tests cover old historical rows from different categories with combined
price boundaries, accurate totals and pagination. Recording Dispatcher tests
cover the category picker, price changes, cancellation, resets, card returns,
legacy links, empty results and access denial. English rendering is checked for
every category-picker section.

This cumulative small patch includes the 4.23.29 price controls and installs
over 4.23.28 or 4.23.29. No new worker, dependency or environment variable is
needed. Validation is local; no live Telegram messages or deployment occurred.
