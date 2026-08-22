# DT PARSER v4.8.6 — Coverage Complete

Основа: v4.8.5 Integrity Recovery / Golden Core.

Главное изменение 4.8.6: integrity определяется по количеству реально подтверждённых target-date страниц. Если пользователь запросил 50 страниц и система действительно собрала 50 валидных страниц, единичный `repeated-content` в одном региональном feed не делает весь scan ложным partial — слабая страница не используется, а глубина добирается другой подтверждённой страницей.

При настоящем shortfall Views не запускаются, partial сохраняет подтверждённые объявления без ложного `0`, а фоновые автозамеры не планируются до полного scan.

Сохранены: resilient 403/429 traffic, Redis runtime isolation, fresh-jobs-first, dynamic page identity, 450ms Page wait, View sharding, Smart Date Hint, unique-page quality и чистый XLSX (`Цена, €` + `👁 Просмотры`).

Подробнее: `DEPLOY_V4_8_6_COVERAGE_COMPLETE.md`.

## v4.8.7 Broadcast Launch

The v4.8.6 known-good parser core is unchanged. Admins now have `📣 Рассылка` with preview + confirmation for text, photo, or photo with caption, plus delivery statistics.

## v4.8.8 — Read-only History Access

Expired subscribers keep read-only access to the main menu and their own saved scans/archive. New scans and network-refresh actions still require an active subscription. Parser core is unchanged.
