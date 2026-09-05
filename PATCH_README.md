# v4.23.6 GitHub patch

Накладывать **поверх v4.23.5** с сохранением путей.

## Что исправляет

- DT Radar AutoScan заимствует свободную мощность, когда нет пользовательских сканов.
- Page Worker prefetch для idle AutoScan расширяется до 20 страниц.
- Exact views используют до x8 локальной concurrency в idle-режиме.
- Большой UNKNOWN exact-tail сначала ремонтируется только по проблемным URL через View Worker fleet, а не отправляет категорию сразу на полный повтор.
- Accurate idle-tail repair расширен до 32 URL маленькими chunks.
- При появлении пользователя новые Turbo repair/prefetch операции прекращаются.
- Добавлен breakdown причин `⚠️ допроверка`.
- Старые failed_categories могут быть классифицированы после deploy без повторного полного круга.

## Не менялось

- 99% exact coverage gate.
- soft tail max 8.
- UNKNOWN остаётся NULL.
- 20 страниц TODAY на категорию.
- DT Score / Organic правила.
- пользовательские scan lanes / FIFO.
- Vinted Lab / Vinted Radar логика v4.23.5.

## Deploy

Redeploy только **Parser / Bot**.

Новых обязательных Railway Variables и ручных SQL-миграций нет.
