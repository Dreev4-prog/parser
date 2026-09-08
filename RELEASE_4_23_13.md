# DT Parser v4.23.13 — Radar Simple UI

Base: exact v4.23.12. Public brand: DT Radar 3.0.

The original user navigation is restored without the extra “Сильные за 48 часов” screen or duration counters on the home card. The existing Горячие, Набирают, Быстро исчезли, Рекорды Radar, Поиск, Категории and Мой Radar remain. Old hot48 buttons and saved navigation contexts open the existing Рекорды Radar screen instead of failing. The technical “Unified 48H” prefix in a product explanation is replaced only in the displayed text; stored evidence is unchanged.

**Retention remains 48 hours.** The six-hour current-demand freshness policy is also unchanged. A stored historical peak is not represented as current HOT; a confirmed disappearance is not restored as an active listing. No Score thresholds, observation schedules, Lifecycle rules, Vinted logic, database schemas, Redis keys or worker contracts are changed. No cleanup or data reset is required.

## Installation

Apply the archive on top of v4.23.12, preserving its internal paths. The changed production files are only `bot.py` and `VERSION`; the other files are release checks and documentation. Redeploy Parser / Bot. The Lifecycle, Page, Date, View and Vinted workers do not need a new deployment for this UI-only change. Do not install this patch over v4.23.11 or use it as a complete repository replacement.

No SQL migration or new Railway variables. Keep the existing database and Redis. A running Radar round may finish normally; there is no need to restart it for this display change. The previous v4.23.12 commit can be restored for an ordinary code-only UI rollback.

## Validation

Verified against a clean local v4.23.12 reconstruction: Python compile, release smoke, runtime global-symbol audit, and the full test suite (259 passed, 167 subtests passed; 85 existing deprecation warnings). Four new UI regressions cover free/paid navigation, home text, old hot48 links and unchanged retention/scoring constants. The final archive is additionally checked by applying it to a clean base and rerunning the suite. No production Railway or marketplace request was made.
