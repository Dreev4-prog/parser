# v4.3.19 — Optional auto-measurements + 15/25/50 pages

## User-visible changes

- +3/+6/+12h automatic control measurements are OFF by default and can be toggled per user.
- The main screen shows the current state and has a dedicated `⏱ Автозамеры` button.
- Turning auto-measurements OFF cancels unfinished scheduled checkpoints for that user.
- Completed measurement history is preserved.
- Manual `👁 Обновить` is always available.
- New scan depth choices: 15 / 25 / 50 pages. 100 pages is removed from the new-scan UI.

## Railway

No new environment variables are required.
Keep the current v4.3.18 dual View Worker / Redis configuration unchanged.

## Core safety

The following v4.3.18 files are unchanged:
- parser.py
- traffic.py
- scan_selection.py
- view_counter_worker.py
- view_manager.py
- service_launcher.py

The database upgrade is additive: `user_settings.auto_observations BOOLEAN DEFAULT FALSE`.
