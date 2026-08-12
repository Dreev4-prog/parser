# Kleinanzeigen Parser Bot v3.0.6

## Exact-date pagination fix

Kleinanzeigen's public result feed can normalize/repeat deep page numbers instead of exposing unlimited pagination. v3.0.6 therefore treats 50 as the verified public page window and never uses repeated page content as a date signal.

The user flow stays unchanged: choose a category, date, and 25/50/100 pages. If the date is inside the nationwide public window, those literal pages are collected. If it is deeper, the bot transparently uses disjoint German state feeds, deduplicates by listing ID, and fills the selected depth-equivalent without exposing regional mechanics in Telegram.

Other v3.0.x functionality (view counts, scan history/dynamics, product recognition, promoted-ad filtering, Moscow-date handling, CSV export) remains in place.
