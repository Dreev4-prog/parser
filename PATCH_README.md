# v4.23.26 — scheduled Telegram promotion posts

Apply on v4.23.25. Includes both approved ALF images and captions:

- 12:00 Moscow: DT Parser;
- 17:00 Moscow: DT Radar;
- existing Daily Radar at 20:00 remains independent, as requested.

Admin panel → ⏰ Автопосты controls posts, daily times, captions, images, buttons,
audiences, previews, individual enabled state, deletion and global pause.
Defaults target registered users without active subscriptions; admins, banned
users and users who muted promotions are excluded.

Tables are added automatically through the existing database initialization.
There are no new dependencies, workers or required Railway variables. Upload
the entire patch INCLUDING the two files inside assets/. See INSTALL_AUTOPOSTS_RU.txt.
