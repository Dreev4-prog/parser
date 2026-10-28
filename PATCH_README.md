# DT Parser 4.23.29 — price range in Radar Records

Small patch for 4.23.28. Upload the included files with replacement, preserving
their paths. See INSTALL_RADAR_RECORDS_PRICE_RU.txt.

Open Radar → Best right now → Records → Price → Custom range.
Enter 100-500, от 100 до 500, up to 100 or 500+. Any price clears the range.
Records has its own budget and retains it through pages and product-card returns.
Filtering uses observed listing prices before counting and pagination. Historical
records retain their Peak Score order. No migration, worker or variable is needed.

Validation: 62 targeted tests passed and release smoke passed. Database tests
cover inclusive bounds, unknown prices, real family prices, counts and pagination.
Telegram updates cover input, reset, navigation and separate filter settings.
No live Telegram messages were sent and no GitHub/Railway deployment was performed.
