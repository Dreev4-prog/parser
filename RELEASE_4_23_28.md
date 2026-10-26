# DT Parser 4.23.28 — English user experience

The old presentation layer replaced phrases and then individual Russian words
across the whole rendered message. It produced mixed copy such as “Лучшие now”
and “only that”, and could rewrite keywords or URL attributes.

The replacement uses a reviewed catalog with one substitution pass. Literal
listing titles and user-entered filters are marked until the final Telegram
boundary. Code blocks and link attributes are preserved. Complete static input
examples can have an explicitly reviewed English version. Category and section
labels, including escaped HTML variants, are localized before button truncation.

Home, onboarding, settings, scan progress/results, Radar feeds/cards/search,
subscriptions, referrals, Daily Radar and error messages have reviewed English
copy. English price phrases are accepted alongside the existing input formats.
XLSX/CSV headings and category labels are localized while product names, values
and links remain unchanged. Background XLSX writers explicitly resolve locale.

Language context records its owner. Sending to another user resolves that
recipient's language, so a task inherited from an administrator cannot force
Russian on an English recipient. The administrator's own admin screens retain
their Russian context.

Home and both default promotional images have separate English assets and
Telegram file-id caches. PromoPost gains caption_en/photo_en through additive,
serialized startup migrations. Standard approved posts are backfilled once;
custom copy is preserved. Admin controls edit and preview English content.
Custom posts without English copy use their main caption/photo, with this state
shown in the admin panel. No runtime machine translation service is introduced.

Daily times, all-users delivery, bounded history retention and Radar scoring are
preserved. No new service, environment variable or dependency is required.

Validation covers rendered public screens, all category labels, listing/query
preservation, recipient-specific delivery, both image caches, English Excel/CSV,
promo creation/edit/preview/sending and an idempotent existing-database upgrade.
Storage retention, the weekly Radar window, navigation and Chromium idle checks
are included in the targeted regression run. Tests use local SQLite and a
recording Telegram session; live Railway/Telegram delivery has not been executed.

Two stale verification fixtures were corrected: the idle test fake now supports
the Redis pipeline used by the guard, and the smoke check validates the active
Railway launcher instead of requiring tracked historical deployment docs to be
absent. Runtime Chromium shutdown logic is unchanged.

Artwork was edited with the built-in image_gen tool using the imagegen skill.
The exact prompts and source/target filenames are in ENGLISH_ARTWORK_PROMPTS.txt.
Final assets: assets/dt_parser_menu_en.png, assets/promo_parser_alf_en.png,
assets/promo_radar_alf_en.png.
