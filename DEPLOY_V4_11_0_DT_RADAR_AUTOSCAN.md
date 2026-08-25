# DT PARSER v4.11.0 — DT Radar AutoScan

v4.11.0 is based on the stable v4.10.2 Page Cache Recovery release and keeps the full DT Radar, four-lane user queue, View Speed Fix and repeated-page/cache recovery.

## New: DT Radar AutoScan

DT Radar can now populate itself even when users do not launch scans.

- scans every real leaf category (group-root aliases are excluded)
- fixed production depth: 15 pages per category
- target date: current Europe/Moscow day at round start
- real view counters are collected by the existing exact Views pipeline
- each category contributes its TOP-12 verified-view listings directly to DT Radar
- AutoScan does not create fake user-facing `UserScan` cards
- Radar signals are idempotent by round/listing, so restart/resume cannot duplicate the same signal

## Two admin modes

### Manual one-shot round

`Admin → 📡 Radar AutoScan → ▶️ Запустить 1 круг`

The bot traverses every leaf category once and then stops automatically.

### Daily round

`🔄 Ежедневный автокруг: ВКЛ/ВЫКЛ`

Preset Moscow launch times: 03:00, 05:00, 08:00, 12:00, 18:00, 23:00.

The daily scheduler performs no more than one automatic round per Moscow calendar day. If enabled after today's configured time and no automatic round has run yet, the round starts at the first available opportunity.

Optional setting: skip the automatic round when a completely successful manual round already finished today.

## Foreground priority

AutoScan is intentionally low priority.

- it does not consume one of the four foreground `scan_worker` queue consumers
- before every new category it checks the foreground running/queued jobs
- if any user scan exists, AutoScan waits
- if a user arrives while an AutoScan category is already running, that small 15-page block finishes and AutoScan yields before starting the next category
- existing Date/Page/View worker architecture is reused

## Persistent progress / restart safety

State is stored in PostgreSQL `app_settings` under `dt_radar_autoscan_v1`.

The persisted state includes:

- round id and mode
- current category index
- target date
- processed/successful/failed category counters
- verified pages
- listings/new listings
- Radar signals added
- daily enabled/time state
- last daily date
- last completed full round date
- last 20 round summaries

A Railway restart resumes a `running` round from the next saved category boundary. A manually stopped round remains `paused` and can be resumed from the admin panel.

## Admin controls

`📡 Radar AutoScan` shows:

- current status
- manual/daily mode
- current category
- progress and percentage
- successful/errors
- verified pages
- listings and new listings
- Radar signals added
- daily on/off
- configured Moscow time
- next automatic launch
- last round summary

Controls:

- `▶️ Запустить 1 круг`
- `⏹ Остановить после категории`
- `▶️ Продолжить круг`
- `🔄 Новый круг`
- `🔄 Ежедневный автокруг: ВКЛ/ВЫКЛ`
- `🕐 Время`
- `✅ Пропускать автокруг после ручного`
- `📜 История кругов`

## Completion notification

Every completed round sends admins a Telegram summary with category success/errors, verified pages, listings, new listings, Radar signals and elapsed time.

## Deployment

Deploy the same v4.11.0 commit to:

- parser
- Page Worker replicas
- Date Worker replicas
- View Worker replicas
- AI Worker

No new Railway variables are required. The daily schedule is controlled from the Telegram admin panel and persisted in PostgreSQL.
