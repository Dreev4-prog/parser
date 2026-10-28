# DT Parser 4.23.29 — Radar Records price range

Records previously omitted the price button, rejected the price screen for list
contexts, discarded the chosen price when loading records, and returned custom
price entry to Radar Home. The existing database query already supported price
constraints on observed listing prices.

Records now exposes the existing price presets and custom range input. List
loading, presets, custom text, pagination and context restoration pass the range
to that query. Selecting a new range resets the page to zero. Product-card back
navigation retains the selected range and previous page. A dedicated session key
keeps the Records budget independent from the existing Search/Categories filter.
The empty result screen explains how to change or clear the range. RU and EN
copy are included, and the input parser also accepts “от 100 до 500”. Custom
price submissions recheck subscription access before reading paid records.

Filtering uses inclusive actual RadarProductListing prices, not an aggregate
min/max envelope. COUNT and pagination use the same predicate. Historical rows
remain visible and keep Peak Score ordering. No database migration, new index,
worker, environment variable or background request is introduced.

Verification exercises real SQL queries against an isolated SQLite database:
lower/upper boundaries, one-sided limits, unknown prices, historical records,
family envelopes without a matching listing, accurate counts and pagination.
Telegram updates run through a recording Dispatcher session to verify custom
input, invalid input, presets, reset, page navigation, card return, separate
budgets and legacy hot48 callbacks. Access expiry is also tested. No live
Telegram message or deployment was performed.
