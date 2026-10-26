# DT Parser 4.23.28 — English UX patch

Install over 4.23.27, preserving folder paths. See INSTALL_ENGLISH_RU.txt.
The patch contains changed source files and three English artwork assets.

Reviewed messages replace word-by-word translation. Listing data, keywords
and HTML URLs remain literal. Background notifications and exports resolve
the recipient's language. Home and promotion images have English editions.
Auto Posts supports separate English text/photo editing and previews.

PostgreSQL adds caption_en and photo_en to promo_posts automatically.
Existing post schedules and administrator edits are preserved. Delivery
remains for all users; the separate Daily Radar schedule is unchanged.

Validation: 51 targeted tests passed; release smoke passed. Coverage includes
English user journeys, sending/admin controls, database upgrade, retention,
weekly Radar statistics, navigation and Chromium idle behavior. No live
Telegram messages were sent and no GitHub/Railway deployment was performed.
