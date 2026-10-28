# DT Parser 4.23.30 — category and price in Radar Records

Install over 4.23.28 or 4.23.29 with file replacement. This archive includes
the previous price filter; installing the 4.23.29 archive first is unnecessary.
See INSTALL_RADAR_RECORDS_FILTERS_RU.txt.

Records → All categories opens section/category selection. Category and price
work together and survive pagination and product-card returns. Resetting one
filter preserves the other. Historical Records retain Peak Score order instead
of switching to the live category feed. RU and EN labels are included.

Validation: 71 targeted tests and release smoke passed. Combined SQL filters,
counts, pagination, UI transitions, resets, English labels and access denial
were checked locally. No migration, dependency, worker or variable is required.
No live Telegram messages or GitHub/Railway deployment were performed.
