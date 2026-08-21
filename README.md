# DT Parser v4.6.7 — Fast UI

Production-clean GitHub package.

## Railway

All services use the same repository and root `railway.json`:

```text
python service_launcher.py
```

`service_launcher.py` selects the current role for Bot / Date Worker / Page Worker / View Worker / AI Worker from the Railway service name (or `DT_SERVICE_ROLE` if explicitly configured).

## Language

A new user chooses a language on the first `/start`:

- 🇷🇺 Русский
- 🇬🇧 English

The choice is saved per user and can later be changed in `⚙️ Настройки / Settings → 🌐 Язык / Language` or with `/language`.

The normal user interface follows the saved language even for admin accounts. The actual admin panel remains Russian.


## Fast UI

- media-menu callbacks no longer waste a Telegram API round-trip on an invalid `edit_text`;
- the main menu reuses Telegram `file_id` after the first image upload;
- independent PostgreSQL reads are parallelized on the home screen, subscription screen and scan start checks;
- `Мои сканы / My scans` performs one archive sweep instead of two and loads list/count in parallel;
- callback spinners are acknowledged before non-critical database reads where safe.

## Included production features

- Product Opportunity Engine
- DT AI Lab
- AI Lab badge notifications
- Idle Chromium memory release after complete fleet idle
- Admin workers/active parsing center
- Russian / English user interface

This clean package intentionally excludes historical deploy notes, tests, Python bytecode caches, and legacy worker entrypoints that are not used by the current production service launcher.
