# DT PARSER v4.8.8 — Read-only History Access

Based on the known-good v4.8.7 Broadcast Launch / v4.8.6 parser core.

## Added

- Users with an expired subscription can open the main menu.
- `📊 Мои сканы` and the archive remain available after expiry.
- Saved scan cards, TOP-12/TOP-50, growth/history and XLSX export remain readable.
- Read-only home clearly shows that the subscription is inactive.
- `🔒 Новый скан` leads to the subscription screen.

## Still requires an active subscription

- New scans.
- Repeat/recheck scans.
- Manual view refresh (network work).
- Categories/settings/auto-observation changes and other active parser functions.

Banned users remain blocked. Admin-only access mode keeps its original semantics.

## Parser core

No parser, Date Worker, Page Worker, View Worker, Redis runtime, traffic, scan integrity, filters, database models or AI parsing behavior changed from v4.8.7/v4.8.6.
