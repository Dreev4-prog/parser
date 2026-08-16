# v4.1.1 — Post-scan crash fix

- Fixed production `NameError: berlin_date_key is not defined` after a fully successful 25-page scan.
- CategoryScanState bookkeeping is now non-fatal: a completed parse can no longer be converted to `partial` by a checkpoint-summary write failure.
- No PostgreSQL migration or new Railway variables.

# DT PARSER v4.1.0 — Universal Date Stream

## Главное изменение

В v4.1.0 удалены разные алгоритмы для «сегодня / вчера / позавчера / старая дата». Любая выбранная дата проходит через один newest-sorted chronology stream.

Ключевой production fix: Railway Browser Fleet теперь разбирает **отрендеренный Chromium DOM (`page.content()`)**, а не сырой `response.text()` navigation-response. Это важно для карточек, метаданные которых гидратируются/нормализуются после DOMContentLoaded.

- единый алгоритм любой даты;
- последовательная проверка от новых объявлений к старым;
- Date Index используется только как проверяемое ускорение;
- `unknown` (частично скрытые timestamps) больше не делает категорию partial;
- partial остаётся только для реальной invalid/challenge/transport ошибки после retries;
- mixed newer/older без target — нормальное подтверждение пересечения даты, а не ошибка;
- если дата действительно глубже 50 страниц, тот же алгоритм применяется к независимым location shards;
- старые distributed result cache инвалидированы namespace `v410:`;
- PostgreSQL migrations не нужны.

# v4.0.4 — Recent Date Stream Fix

- Сегодня / вчера / позавчера больше не используют отдельный поиск даты: nationwide-выдача идёт последовательно от страницы 1.
- Страницы новее выбранного дня пропускаются, выбранный день собирается, после перехода на более старую дату проход завершается.
- Глубина 25/50/100 теперь отсчитывается от фактического начала выбранной свежей даты, а не от page 1.
- Страницы с частично скрытыми timestamp не превращают свежий скан в partial; реальные invalid/challenge страницы остаются строгими ошибками.

# v4.0.2 — Today Fast Path & Partial Cache Fix

- Текущая дата больше не проходит отдельный date-locator: скан начинается с page 1 newest feed.
- Partial ScanResult больше никогда не используется как общий 5-минутный cache.
- Новый cache namespace `v402:` автоматически отсекает старые poisoned v4.0/v4.0.1 результаты.
- Force refresh очищает distributed result cache.
- Sparse timestamps на сегодняшних страницах не обрушивают всю категорию в partial.
- Partial + 0 results больше не показывается пользователю как доказанный ноль.

# v4.0.1 — Tolerant Chronology Fix

- page chronology is evidence-based instead of requiring 55–60% timestamp coverage;
- an exact target date is accepted even on mixed card templates;
- two dated cards can establish newer/older direction;
- weak pages no longer automatically trigger the huge regional fallback;
- hidden/regional fallback is reserved for dates genuinely beyond the public window;
- broader metadata selectors improve publication-date extraction across Kleinanzeigen card templates;
- Browser Fleet architecture from v4.0 remains unchanged.

# DT PARSER v4.0.0 — Railway Browser Fleet

## v4.0.0

- Новый production entry point `python fleet_worker.py`.
- Один Railway replica держит один долгоживущий Chromium process.
- По умолчанию 2 независимых BrowserContext на replica (`FLEET_CONTEXTS_PER_REPLICA=2`).
- На Hobby можно использовать 6 replicas: до 12 browser contexts при одном Chromium process на container.
- BrowserContext создаётся на конкретный scan job и закрывается после него; browser process остаётся прогретым.
- `SHARE_ACTIVE_CATEGORY_SCANS=0`: медленный активный пользователь не удерживает другого как subscriber.
- Redis остаётся глобальным governor/circuit breaker; вычислительная мощность и внешний сетевой concurrency регулируются отдельно.
- Просмотры остаются вне foreground scan path.
- Stable Engine/PostgreSQL checkpoints сохранены.
- Railway config: `railway.fleet-worker.json`.
- Подробная инструкция: `DEPLOY_V4_BROWSER_FLEET.md`.

---

## v3.8.0

- Новый Stable Scan Engine: общая category/date/depth работа между пользователями.
- Последовательный поиск даты вместо jump/binary как основного алгоритма.
- PostgreSQL checkpoints подтверждённых страниц и persistent date index.
- Повторяется только слабая страница; recovery не начинает категорию с нуля.
- Одинаковые активные сканы снова объединяются и зеркалят общий прогресс через Redis.
- Разные depth jobs переиспользуют общие page checkpoints.
- Просмотры вынесены из обычного foreground scan в immediate baseline `views-worker` + 3/6/12h.
- `Min Views > 0` автоматически оставляет foreground view collection для корректного фильтра.
- Stable Engine скрывает ручную «Допроверку» из нормального пользовательского flow.
- Новые DB-таблицы создаются автоматически; существующие данные сохраняются.
- Новый Railway entry point: `python stable_worker.py`.

Подробная установка: `DEPLOY_V3_8_STABLE.md`.

---

# Kleinanzeigen Parser Bot v3.7.2

## v3.7.2 — Stability Core

Цель релиза — перестать отдавать пользователю постоянный `partial` после единичного
сетевого/качественного сбоя и сделать зависание на «Поиск даты» наблюдаемым.

- **HTTP-first hybrid:** обычный foreground category request сначала идёт через лёгкий persistent HTTP. Chromium запускается только при transport/JS compatibility failure.
- **Hard watchdog:** один HTTP request не может незаметно висеть дольше `HYBRID_WATCHDOG_SECONDS` (по умолчанию 15 сек.). После watchdog transport выполняет ограниченный fallback/retry.
- **Verified page checkpoints:** сильные подтверждённые category pages сохраняются внутри parser-session текущего UserScan. Слабые, suspicious и low-date-coverage ответы намеренно не кэшируются.
- **Automatic partial recovery:** если основной проход вернул `date_complete=False`, бот до финального результата автоматически выполняет до двух recovery-pass. Они обходят готовый category-result cache, но переиспользуют verified page checkpoints, поэтому цель — дополучить проблемные участки, а не повторить всю работу.
- Ручная **«Допроверка»** остаётся только аварийным вариантом после исчерпания автоматических recovery-pass.
- Во время поиска даты UI показывает текущий transport (`HTTP-first`, `HTTP после browser-сессии`, `Browser fallback`), время ожидания ответа Kleinanzeigen и число watchdog timeout.
- Явные `403/429`/challenge по-прежнему не обходятся сменой транспорта.
- PostgreSQL schema не менялась; новой миграции нет.

Рекомендуемый production service остаётся `hybrid-worker`; подробности — в `DEPLOY_V3_7_2_STABILITY.md`.

---

## v3.7.1 — Fair Network Lanes

- 5 hybrid Railway replicas now pace foreground requests independently instead of competing for one shared Redis next-request timestamp.
- Global Redis concurrency caps remain shared, so independent lanes do not remove the safety ceiling.
- `HYBRID_SCAN_LANES=5` and `HYBRID_GLOBAL_LANES=8` form the hybrid-worker production profile and override stale generic limits inside that worker process.
- Fix targets the symptom where one scan advances while other active scans remain on date search.


## v3.7.0 — Browser → HTTP Hybrid Transport

Экспериментальный production-профиль для multi-user нагрузки: Chromium больше не
рендерит каждую страницу категории. Один user scan открывает первую страницу через
обычный headless Chromium, переносит `BrowserContext.storage_state()` в отдельный
Playwright `APIRequestContext`, после чего Chromium освобождается из RAM, а основной
поиск даты и проход страниц продолжается лёгкими HTTP-запросами с той же сессией.

### Что изменилось

- Добавлен `hybrid_worker.py` и Railway-конфиг `railway.hybrid-worker.json`.
- Новый `SCAN_TRANSPORT=hybrid`: browser seed → API HTTP bulk → browser compatibility fallback.
- Chromium по умолчанию закрывается сразу после получения storage state (`HYBRID_CLOSE_BROWSER_AFTER_SEED=1`).
- Сессионные cookies продолжают жить в standalone `APIRequestContext`; при совместимом browser fallback актуальное состояние переносится обратно.
- Явные `403/429` и challenge-страницы **не обходятся переключением транспорта**: worker публикует backoff/cooldown и завершает проблемный шаг контролируемо.
- HTTP transport имеет короткие retries только для сетевых/5xx сбоев; после этого разрешён ограниченный browser compatibility fallback.
- Direct view-counter в hybrid worker тоже переиспользует лёгкий request context и не поднимает Chromium без необходимости.
- Active scans в hybrid-режиме не объединяются в один владеющий scan, поэтому каждый пользователь получает отдельный worker lane. Завершённый DB-cache остаётся.
- В live progress отображается `⚡ Browser → HTTP hybrid`, чтобы тестировать реальный режим, а в Railway logs есть `Hybrid session seeded` и `compatibility fallback`.
- PostgreSQL/Redis schema не менялись.

### Рекомендуемая схема

```text
Telegram bot ×1
      │
      ├── PostgreSQL
      └── Redis Streams
             │
       ┌─────┼─────┬─────┬─────┐
       ▼     ▼     ▼     ▼     ▼
    Hybrid Hybrid Hybrid Hybrid Hybrid
    Worker Worker Worker Worker Worker
      │      │      │      │      │
  Chromium используется коротко для seed/fallback
      │      │      │      │      │
      └──── bulk category work = lightweight HTTP ────┘

      + views-worker ×1
```

Для Railway: **`DEPLOY_V3_7_HYBRID.md`**.

---

## v3.6.0 — Browser-Isolated Workers

Релиз для реального multi-user browser execution. В v3.5.0 Redis уже разделил очередь и worker-процессы, но foreground category/date search всё ещё выполнялся обычным `httpx`. Поэтому пять worker replicas могли логически быть разными, но сетевой путь поиска даты оставался одинаковым.

### Что изменилось

- Добавлен `browser_worker.py`: отдельный Railway worker запускает foreground category pages через **реальный headless Chromium / Playwright**.
- Один активный UserScan получает один `KleinanzeigenParser` + один browser context, который переиспользуется для всех выбранных категорий этого запуска. После завершения браузер полностью закрывается.
- При 5 browser-worker replicas одновременно могут существовать **5 независимых Chromium-процессов/контекстов** — по одному на активный пользовательский scan.
- Browser transport используется именно для поиска даты и чтения category pages; HTML дальше разбирается тем же проверенным parser-quality кодом.
- Изображения, шрифты, media и CSS в Chromium блокируются, чтобы category navigation не тратила лишний трафик/RAM.
- Активные одинаковые сканы больше не принудительно объединяются (`SHARE_ACTIVE_CATEGORY_SCANS=0` по умолчанию у browser worker). Каждый пользователь действительно продолжает собственный browser scan. Завершённый короткий cache сохраняется.
- 403/429 в одном browser worker больше не публикует общий Redis freeze для всех browser workers (`DIST_TRAFFIC_SHARED_COOLDOWN=0`). Локальный worker аккуратно замедляется/останавливает проблемную категорию, остальные продолжают работу.
- Глобальный Redis concurrency governor сохранён: независимые браузеры не получают право бесконтрольно создавать burst-запросы.
- Парсер теперь переиспользуется на уровне всего UserScan через `ContextVar`, поэтому Chromium не перезапускается между каждой из 1–5 категорий пользователя.
- Старый `parser_worker.py` оставлен как HTTP fallback; можно переключаться без миграции PostgreSQL.

### Рекомендуемая схема

```text
Telegram bot ×1
      │
      ├── PostgreSQL
      └── Redis Streams
             │
       ┌─────┼─────┬─────┬─────┐
       ▼     ▼     ▼     ▼     ▼
   Browser Browser Browser Browser Browser
   Worker1 Worker2 Worker3 Worker4 Worker5
   Chromium Chromium Chromium Chromium Chromium

      + views-worker ×1
```

Для Railway подробная инструкция: **`DEPLOY_V3_6_BROWSER_WORKERS.md`**.

> Важно: разные Chromium-сессии дают независимые процессы/cookies/event loops, но сами по себе не гарантируют разные публичные egress IP. Если внешний сайт ограничивает именно общий IP/ASN, это отдельное сетевое ограничение, а не проблема очереди или браузерной изоляции.

---

## v3.5.0 — Multi-User Core / Redis Workers

Это архитектурный релиз для стабильной одновременной работы нескольких пользователей. Старый односервисный режим сохранён как fallback (`DISTRIBUTED_WORKERS=0`), но production-рекомендация — отдельные сервисы bot + parser-worker + views-worker, общий PostgreSQL и Redis.

### Что изменилось

- **Telegram bot больше не обязан сам парсить.** В distributed-режиме `bot.py` занимается интерфейсом, пользователями, платежами и постановкой задач; реальные сканы выполняет `parser_worker.py`.
- **Redis Streams — постоянная очередь.** Задание ACK-ается только после завершения. Если worker-процесс аварийно умер, pending job может быть автоматически забран другой репликой через `XAUTOCLAIM`.
- **Защита от двойного запуска одного UserScan.** Короткий Redis job-lock с heartbeat не даёт двум worker-репликам одновременно выполнять один и тот же job UID.
- **Shared Category Scan теперь межпроцессный.** Одинаковые `категория + дата + глубина` между разными пользователями/worker-репликами объединяются через Redis lock/result cache. Второй пользователь ждёт общий результат вместо повторного crawl.
- **Живой прогресс общего скана передаётся между replicas.** Worker-владелец публикует `CategoryLiveProgress` в Redis, подписчик зеркалит его в свою Telegram-карточку.
- **Глобальный Redis traffic governor.** Лимиты `scan/view/browser/global` применяются ко всем parser replicas вместе, а не отдельно в каждом контейнере. Первый 403/429 публикует общий cooldown, который видят все workers.
- **Фоновые +3/+6/+12ч замеры вынесены в `views_worker.py`.** Они больше не конкурируют с Telegram polling и основными parser workers в одном event loop.
- **PostgreSQL остаётся source of truth.** Redis хранит очередь, locks, краткий cache/progress; объявления, сканы, платежи и история по-прежнему живут в PostgreSQL.
- **Distributed Stop.** `/stop` и кнопка остановки ставят cancel signal в Redis; running-worker подхватывает его примерно за секунду. Queued-job отменяется в PostgreSQL сразу, поэтому пользователь может запускать новый скан.
- **Меньше DB connections на replica.** При `DISTRIBUTED_WORKERS=1` SQLAlchemy по умолчанию использует pool `3 + 2 overflow` вместо `5 + 5`, чтобы 5 parser replicas + bot + views-worker не раздували соединения PostgreSQL.
- В админской статистике отображаются distributed-режим, число живых parser/views workers, running и queued scans.

### Рекомендуемая production-схема

```text
Telegram
   │
   ▼
DT PARSER BOT ×1
   │
   ├──────────────► PostgreSQL
   │
   ▼
Redis Streams / locks / cache / traffic governor
   │
   ├──► parser-worker ×5
   │
   └──► views-worker ×1
```

Для пяти одновременных пользовательских сканов рекомендуется **5 parser-worker replicas с `PARSER_WORKER_CONCURRENCY=1`**. Это легче контролировать и восстанавливать, чем один контейнер с пятью тяжёлыми задачами.

Подробная настройка Railway: **`DEPLOY_V3_5_MULTIUSER.md`**.

---

## v3.4.3 — брендированное главное меню

- `/start`, `/menu` и кнопки **«🏠 Меню»** теперь открывают фирменную карточку **DT PARSER** с основной русскоязычной иллюстрацией.
- Навигационные inline-кнопки находятся прямо под изображением: **Новый скан**, **Популярное**, **Мои сканы**, **Категории**, **Настройки**, **Подписка**.
- Для администраторов в фирменном меню дополнительно появляется **«🛠 Админ-панель»**.
- Картинка используется только для главного меню; рабочие экраны категорий, настроек, сканов, платежей и аналитики остаются компактными текстовыми экранами.
- Возврат в меню из CSV/документа безопасно отправляет отдельную фото-карточку и не пытается редактировать файл.
- Изображение хранится локально в `assets/dt_parser_menu.png`, поэтому внешний хостинг/URL и новые Railway Variables не нужны.
- Telegram Commands Menu теперь содержит `/menu` как явную команду главного экрана; `/start` по-прежнему работает и сохраняет onboarding для нового пользователя.
- Ядро парсинга, PostgreSQL, фильтры, платежи, Stop/Recovery, архив и автозамеры не менялись.


## v3.4.0 — Design & UX Update

Релиз без изменения ядра парсинга: структура экранов и кнопок приведена к единому продуктовому стилю.

- Главное меню стало короче: одна главная кнопка **«▶️ Новый скан»**, затем **Популярное / Мои сканы**, **Категории / Настройки** и Подписка. Постоянная кнопка «Текущий результат» убрана; старый `/result` сохранён только для обратной совместимости.
- Брендинг пользовательских экранов унифицирован как **Kleinanzeigen Analytics**.
- Настройки больше не показывают семь режимов одновременно: текущий режим открывается отдельным компактным выбором, а цена, просмотры, дубли, очистка, сортировка и слова сгруппированы по смыслу.
- Запуск скана оформлен как два шага: **1/2 Дата** → **2/2 Проверка запуска и глубина**. Перед стартом видны только реально важные активные фильтры.
- Live-progress упрощён до «подготовка → поиск даты → сканирование %» с категорией, страницами, объявлениями и временем.
- После завершения остаётся короткая карточка: дата, категории, количество объявлений в результате, время и автозамеры 3/6/12ч. CSV отправляется отдельным сообщением ниже с компактными действиями.
- Карточка скана и «Популярное» получили более короткие кнопки; второстепенные действия перенесены внутрь карточки скана.
- Telegram Menu очищен от редко нужного `/result`; команда продолжает работать для старых сообщений/пользователей.
- Исправлен лишний двойной ответ команды `/popular`.
- PostgreSQL, фильтры, платежи, лимит 5 категорий, Stop/Recovery, архив и автозамеры не менялись. Новых Railway Variables нет.


## v3.3.2 — минимум просмотров + актуальное «Популярно сейчас»

- В **⚙️ Настройки** добавлен фильтр **«👁 От просмотров»**: без порога / 10+ / 25+ / 50+ / 100+ / своё значение.
- При ненулевом пороге в пользовательский результат попадают только объявления с успешно полученным публичным счётчиком просмотров не ниже порога. Фильтр применяется к сохранённому скану, выгрузкам, TOP и «Популярно сейчас».
- Порог просмотров не уменьшает количество сетевых обращений: чтобы понять, прошло ли объявление порог, бот сначала должен получить его счётчик просмотров.
- PostgreSQL автоматически получает `user_settings.min_views INTEGER DEFAULT 0`; новых Railway Variables не требуется.
- **«🔥 Популярно сейчас» больше не объединяет историю.** Для выбранной категории используется только последний **успешно завершённый** (`done`) скан.
- Если более новый запуск был остановлен, упал или завершился частично, он не заменяет последний полноценный скан в «Популярно сейчас».
- «Самые просматриваемые», TOP 3/6/12ч и TOP-50 внутри «Популярно сейчас» считаются только по этому последнему успешному скану. Предыдущие запуски остаются доступны через «Мои сканы» и «Архив».



## v3.3.1 — лимит категорий и точная допроверка

- Один пользовательский запуск теперь ограничен максимум **5 категориями**. В выборе всегда виден счётчик `X/5`; шестая категория не добавляется, пока пользователь не снимет одну из выбранных.
- Массовый выбор подкатегорий заполняет только свободные места до лимита 5 и не создаёт скрытый запуск на 10–15 категорий.
- Старые сохранённые выборы с более чем 5 категориями не запускаются молча: бот просит убрать лишние или очистить выбор.
- Региональный fallback исторического поиска считает глубину по **реально проверенным страницам выбранной даты**, а не по формуле «25 оставшихся после фильтрации объявлений = 1 страница». Это уменьшает ложные `partial` после удаления магазинов, promoted/bumpup и дублей.
- Частичный скан сохраняет точные ключи проблемных категорий в PostgreSQL (`user_scans.incomplete_category_keys`).
- Вместо длинной технической простыни пользователь получает короткое сообщение: сколько категорий требуют дополнительной проверки и что уже найденные данные сохранены.
- Добавлена кнопка **«🔄 Допроверить проблемные категории»**. Она повторно запускает только неполностью проверенные категории с той же датой и глубиной, не трогая уже успешные категории и не переиспользуя свежий partial-cache.
- Миграция PostgreSQL аддитивная: новую колонку бот создаёт сам при старте, ручной SQL не нужен.

## v3.3.0 — Production Ready

Это релиз доводки продукта для конечного пользователя. Он сохраняет весь функционал v3.2.8 и добавляет пять production-блоков.

### 1. Первый запуск / onboarding

- Новый пользователь получает короткое обучение: что делает сервис → как выбрать категории/настройки → что произойдёт после скана.
- После прохождения onboarding больше не показывается при каждом `/start`.
- Пользователи, существовавшие до v3.3.0, не заставляются проходить обучение повторно; его можно открыть вручную через `/help` → **🎓 Показать обучение**.

### 2. Устойчивость к Railway/Kleinanzeigen

- `UserScan` хранит `chat_id`, Telegram status message, число восстановлений/retry и последнюю ошибку в PostgreSQL.
- После перезапуска Railway незавершённый `queued/running` скан автоматически возвращается в очередь. Уже записанные объявления/история в PostgreSQL не удаляются.
- Нажатие **Stop** теперь сразу записывает состояние `cancelling` в БД. Если процесс упадёт в этот момент, такой скан завершится как `cancelled`, а не воскреснет после рестарта.
- Поверх HTTP retry/backoff парсера добавлена одна контролируемая повторная попытка категории (`SCAN_CATEGORY_ATTEMPTS=2` по умолчанию).
- При окончательном сбое пользователь получает понятную карточку и кнопку **🔄 Повторить этот скан**.

### 3. Полный цикл подписки

- Активную подписку можно продлевать заранее; купленные дни добавляются к уже оставшемуся сроку.
- Добавлен раздел **💳 Мои платежи** со статусами счетов, быстрым открытием и ручной проверкой ожидающего платежа.
- Истёкший/ошибочный счёт предлагает создать новый, а не оставляет пользователя в тупике.
- Бот один раз предупреждает примерно за 24 часа до окончания подписки и один раз уведомляет после окончания.
- Сохранённые сканы при окончании подписки не удаляются.

### 4. Админ-контроль пользователей

Карточка пользователя в `/admin` теперь даёт без доступа к PostgreSQL:

- поиск по ID / username / имени;
- статус и срок подписки;
- +1 / +3 / +7 / +30 дней и **свой срок**;
- отзыв доступа и блокировку;
- последние платежи;
- последние сканы и количество автоматических восстановлений;
- последние ошибки парсера/сканов.

### 5. UX cleanup

- В пустом «Популярное сейчас» есть прямой переход к первому скану.
- Из категории и настроек можно сразу перейти к запуску.
- На выборе глубины есть понятный возврат к выбору даты.
- `/help` стал точкой повторного обучения вместо технической справки.
- Экран подписки больше не перегружает обычного пользователя внутренним режимом доступа и предлагает понятные действия по продлению/платежам.

### Миграция

Новых обязательных Variables нет. PostgreSQL сам получит additive-поля при старте v3.3.0. Для существующих пользователей onboarding помечается пройденным; новые пользователи увидят его один раз.

**Первый переход с v3.2.8:** делай deploy, когда нет активного скана. Старые незавершённые v3.2.8-запуски ещё не содержат Telegram recovery metadata (`chat_id`/`status_message_id`), поэтому автоматическое восстановление гарантируется для запусков, созданных уже на v3.3.0.

Опциональные настройки:

```env
SCAN_CATEGORY_ATTEMPTS=2
SCAN_CATEGORY_RETRY_SECONDS=4
SUBSCRIPTION_NOTICE_POLL_SECONDS=300
```


## v3.2.8 — автозамеры 3/6/12ч + архив сканов

- Автоматические контрольные замеры просмотров теперь выполняются только через **+3 / +6 / +12 часов**. +1ч и +24ч удалены из планировщика, интерфейса TOP роста и новых задач.
- При обновлении незавершённые старые +1ч/+24ч задачи очищаются автоматически; уже сохранённая история не удаляется.
- «📊 Мои сканы» работает как входящие: завершённый скан остаётся там 24 часа после завершения, затем автоматически перемещается в персональный «📦 Архив».
- Архивирование не удаляет `UserScan`, `ScanListing` или историю просмотров: архивные сканы продолжают участвовать в «🔥 Популярное сейчас», TOP и выгрузках.
- Добавлена кнопка **«🧹 Очистить и переместить в архив»** — она мгновенно убирает завершённые сканы из основного списка; queued/running задачи не трогает.
- «📦 Архив» пагинируется по 8 сканов на страницу, поэтому даже большая история не превращается в тысячи кнопок.
- Фоновая уборка выполняется каждые 15 минут, а также при открытии «Моих сканов»/архива.

## v3.2.7 — дата только при запуске

- Убран пункт «Период результата / Сегодня» из настроек парсинга.
- Дата теперь выбирается только перед запуском: «Сегодня», «Вчера» или «Выбрать дату».
- Старое значение `user_settings.period` оставлено в PostgreSQL только ради совместимости схемы и больше не влияет на фильтрацию.
- Цена, режим, умные дубли, очистка шума, сортировка и include/exclude применяются одинаково к любой выбранной дате.
- Перед выбором глубины 25/50/100 бот показывает дату и полный набор активных настроек.
- Старые Telegram-кнопки «Период», оставшиеся в ранее отправленных сообщениях, безопасно возвращают пользователя в новые настройки и ничего скрыто не меняют.

## v3.2.6 — единые настройки результата

- Настройки пользователя теперь применяются к сохранённому результату скана, а не только к отдельной выгрузке: цена, очистка шума, include/exclude, умные дубли, сортировка и режим «Уникальные».
- Сырой crawl остаётся в общей базе для аналитики, но `ScanListing` сохраняет только пользовательскую отфильтрованную выборку.
- Точный поиск по дате (например, 10.08) имеет приоритет над относительным «Периодом результата», поэтому исторический скан не исчезает из-за настройки «Сегодня».
- Перед выбором 25/50/100 страниц бот показывает активные настройки и явно пишет, какая дата будет использоваться.
- Исправлен порядок «Уникальные + умные дубли»: уникальность считается до схлопывания дублей, поэтому повторяющийся товар больше не превращается в ложный «уникальный».
- Эти же фильтры применяются к старым сохранённым сканам при открытии карточки, TOP просмотров, TOP роста и «Популярное сейчас».

## v3.2.5 — Popular по всем сканам категории

- «Популярное сейчас» агрегирует все завершённые сканы одной категории, а не только последний запуск.
- Разные даты (например, 11.08 и 12.08) объединяются; одно и то же объявление не дублируется.
- Сводная логика используется для самых просматриваемых, TOP роста и TOP-50 выгрузок.

## v3.2.2 — PostgreSQL Production

- Railway production now **requires PostgreSQL**. The bot no longer silently falls back to an ephemeral SQLite file when `DATABASE_URL` is missing.
- Railway `postgres://` / `postgresql://` URLs are normalized automatically for SQLAlchemy + asyncpg.
- Startup waits/retries while a newly-created Railway PostgreSQL service becomes reachable.
- PostgreSQL connection pooling is enabled with conservative defaults (`5 + 5` connections) and `pool_pre_ping`.
- All existing parser, scan history, view observations, users, subscription plans, invoices/payments and admin settings use the same PostgreSQL database through SQLAlchemy.
- Additive schema migrations remain automatic on startup.
- Local SQLite remains available only as a development/test fallback outside Railway.

### Railway PostgreSQL setup

1. In the Railway project choose **New → Database → PostgreSQL**.
2. Open the **parser** service → **Variables**.
3. Add `DATABASE_URL` with value `${{Postgres.DATABASE_URL}}` (replace `Postgres` only if your database service has a different name).
4. Redeploy the parser service.
5. Open `/admin` → database/parser statistics and verify the backend is `PostgreSQL`.

No PostgreSQL username/password/host variables need to be copied separately when `DATABASE_URL` references the Railway Postgres service.


## v3.2.1 — Telegram Menu

- Added the standard Telegram **Menu** button with ready-to-tap commands; users no longer need to type commands manually.
- User commands: `/start`, `/new_scan`, `/my_scans`, `/popular`, `/categories`, `/settings`, `/subscription`, `/result`, `/help`.
- Admin chats additionally see `/admin`.
- Command handlers open the same existing screens and workflows as the inline buttons, so parser/payment logic remains unchanged.
- `/subscription` stays reachable for users without active access.

## v3.1.7 — Clean Live Progress

- Simplified the live scan message to the essentials: current category, selected-date search state, page progress, listing count, view count, elapsed time, and one progress bar.
- Hidden start-page numbers, date-coverage telemetry, parser quality diagnostics, internal phase labels, and other implementation details from the normal live UI. Diagnostics remain available in logs and final quality/warning handling.
- Removed the extra “Launching scan” message. A scan now creates one live status card that changes from preparation → date search → collection.
- Multi-category progress remains visible as `current/total`, while each category still performs its own independent date search.


## v3.1.6 — Clean Scan UI

- Removed the user-facing **Models** section/button. Recognition data may remain internal for future analytics, but it is not shown in everyday scan UX.
- Compact scan card: date/depth/quality, result, observation. Successful diagnostic noise is hidden.
- Simplified actions: **Update views**, **Top views**, **Top growth**, **Repeat scan**, **History**, **Download result**.
- Model labels were removed from Telegram Top/Top growth and from the TOP-50 growth workbook.
- Legacy Model buttons from old messages safely return to the scan card.


## v3.1.3 — 403 Recovery / Safe Multi-User

- A 403/429 during date location no longer immediately ends the category with zero successful pages.
- Interactive category-page requests may wait up to 180 seconds (configurable) behind the shared circuit breaker and retry after quiet cooldowns.
- Every refusal reduces process-wide concurrency; background view checkpoints also wait behind the same gate.
- Safer default network limits: 3 category requests, 4 view requests, 1 browser fallback, 7 total. `MAX_CONCURRENT_JOBS` may remain 4; the network gate decides how many requests actually run at once.
- If the public site is still refusing requests after the full recovery window, the current category is partial and the remaining categories in that same job are not hammered one-by-one. Existing successful category results remain saved.
- This is graceful backoff only; the release does not attempt to bypass Kleinanzeigen protections.


## v3.1.2 — Adaptive Traffic Manager

This release keeps v3.1.1 multi-category/date reliability and adds a process-wide
traffic controller for commercial multi-user operation.

- **4 user scan workers by default.** Different users can genuinely scan in parallel.
- **Separate request pools:** category pages, direct view counters, and Chromium fallback.
- **Interactive capacity reservation.** Automatic +3/+6/+12h checkpoints keep working,
  but while scans are active their direct-view concurrency is reduced so they cannot starve a new scan.
- **Global burst smoothing.** Page/view/browser requests from all parser instances are spaced,
  instead of every worker releasing a burst at the same instant.
- **Adaptive 403/429 circuit breaker.** A refusal lowers effective concurrency and starts one shared
  bounded cooldown for the whole process. After a quiet period and enough successful requests,
  capacity grows back automatically. The code does not attempt to bypass site protection.
- **Shared scans and cache remain enabled.** Identical category + date + depth requests reuse one
  in-flight scan/result rather than multiplying network traffic.
- **Actual measurement time remains authoritative.** Background popularity checkpoints can be delayed
  by traffic pressure without pretending they ran at an exact clock second.

Recommended starting values are included in `.env.example`; v3.1.6 uses real direct-only control measurements and lower view concurrency. Tune upward only after observing real 403 rate and latency.

## v3.1.1 — Multi-category isolation fix

- Every selected category performs its own independent date-location cycle.
- Large categories no longer accept a zero result from a truncated >50-page feed.
- If an official state feed is still too large, the parser automatically drills into smaller official location feeds discovered from that category page.
- Hidden location feeds are internal only; the user still sees category + date + 25/50/100 pages.
- Progress now clearly shows the current category number (for example 2/3).
- A category that cannot be fully verified is marked partial instead of silently becoming zero.


## Parser Quality & Stability

v3.1.1 is a reliability release built on v3.0.7. Popularity Tracker, automatic
3/6/12-hour view checkpoints, product recognition, My Scans and the
25/50/100 depth workflow are preserved.

### What changed

- **No false zero from weak dates.** A result page may prove that the selected
  calendar day is newer/older/absent only when enough listing cards have a
  trustworthy publication date. If date coverage is weak, the scan is marked
  partial instead of returning a confident zero.
- **Page identity verification.** The parser checks result offsets and the final
  pagination URL. Redirected/normalized pages are not used as chronology data.
- **Repeated-page protection.** Compact listing-ID fingerprints detect when two
  requested pages contain the same result set. Repeated content is rejected as a
  date signal.
- **Narrow date extraction.** Publication dates continue to be read from listing
  metadata rather than arbitrary card/title text.
- **Conservative promoted-ad filter.** Only explicit card-level promotion markers
  are removed; generic words in titles/layout are not used as a filter.
- **Quality telemetry.** Every real category response records raw cards, parsed
  listings, missing dates/prices, promoted cards, duplicates, invalid/repeated
  pages, view failures and a 0–100 quality score.
- **Persistent quality score.** New saved scans show their parser-quality score
  in `📊 Мои сканы`. Old scans display that no v3.1.1 quality measurement exists.
- **Better partial results.** A category exception, an unverified date boundary,
  or inaccessible feed now marks the saved scan partial instead of silently
  completing it.
- **Admin stats.** `📊 База и парсинг` now includes average v3.1.1 quality, missing
  dates, invalid pages and repeated pages for the current day.

### Exact-date behavior

The user still chooses:

1. category;
2. Moscow calendar date;
3. depth: **25 / 50 / 100**.

For recent dates the parser uses the verified public feed directly. If the date
is beyond Kleinanzeigen's public pagination window, the existing hidden shard
mechanism is used internally. These internal feeds are never shown to end users;
unique target-date listing IDs are merged into one saved scan.

A zero-result scan is considered complete only after the date boundary was
verified with reliable publication-date data. Otherwise the result is explicitly
`partial`.

### Automatic popularity measurements

Unchanged from v3.0.7: completed scans get public-view checkpoints at
`+3 / +6 / +12` hours. Category-separated growth TOPs show TOP-10 in
Telegram and can export TOP-50 XLSX.

### Parser-quality self tests

The archive contains `tests/test_parser_quality.py`. It covers:

- a date inside a product title not overriding the publication timestamp;
- promoted-card filtering;
- normalized page rejection;
- low date coverage becoming `unknown`;
- one-card target boundary handling;
- reliable newer/older direction classification.

Run locally with:

```text
python -m unittest discover -s tests -v
```

### Deployment

Railway start command remains:

```text
python bot.py
```

`DATABASE_URL` is required on Railway in v3.2.2. PostgreSQL databases are upgraded
with additive columns on startup. Optional v3.1.1 tuning:

```text
MIN_PAGE_DATE_COVERAGE=0.55
MIN_PAGE_DATED_ITEMS=3
```

On Railway v3.2.2+ uses PostgreSQL only. SQLite is retained solely for local
development/testing outside Railway.


## v3.1.4 — Lightweight Views Engine + Responsive UI

- Массовые первичные и повторные замеры просмотров используют только быстрый direct HTTP counter. Chromium/browser fallback больше не запускается для сотен объявлений автоматически.
- Неудачные direct-счётчики помечаются как «без данных» и не тормозят весь скан. Browser fallback оставлен только для точечной диагностики одного объявления.
- Повторные замеры идут пакетами (по умолчанию 40 ID с короткой паузой), а автоматический observation worker по умолчанию один.
- `👁 Обновить просмотры` запускает фоновую задачу и мгновенно возвращает управление Telegram. Можно сразу открывать другие меню и запускать сканы.
- Повторное нажатие для того же скана не создаёт второй сбор. После окончания бот сам присылает уведомление и кнопки к динамике/скану.
- Фоновые ручные и автоматические замеры проходят через один лёгкий collector. После первого задания следующее заново проверяет DB cache, поэтому одинаковые ID из пересекающихся пользовательских сканов не запрашиваются повторно в течение 5 минут.
- Общий DB cache `views_checked_at` продолжает переиспользовать свежие значения между пользователями и пересекающимися сканами.


## v3.1.6 — Real View Snapshots + Animated Background Progress

- Manual `👁 Обновить просмотры` runs as a true background task and immediately shows a separate live percentage/progress message.
- The Telegram UI remains usable while the measurement runs; progress edits are throttled to roughly once every 1.5 seconds / batch.
- Manual and scheduled 3/6/12h checkpoints require genuinely fresh Direct View values. The normal multi-minute cache can no longer create a fake observation point.
- A tiny `VIEW_MEASUREMENT_REUSE_SECONDS` window (20s default) only coalesces truly simultaneous measurements across users.
- If zero fresh values are available, no new observation point is created.
- Completion replaces the progress message with a clear summary: fresh values, direct requests, simultaneous reuse, missing counters, listings that grew, maximum and total growth.
- Chromium/browser fallback remains disabled for mass measurement jobs.

## v3.1.8 — Private sellers only

- Commercial/store listings are excluded through Kleinanzeigen's official `Anbieter: Privat` search filter.
- Store ads no longer consume the selected 25/50/100-page depth.
- They are not saved into scan snapshots and therefore do not enter view refreshes, popularity or growth TOPs.
- No extra detail-page requests are needed; the filter is applied to the category/search URL itself.
- Set `FILTER_BUSINESS_SELLERS=0` only if commercial sellers should be included again.

## v3.2.0 — Admin & Subscriptions

The parser core from v3.1.8 is unchanged. v3.2.0 adds a commercial access layer around it.

### Admin panel

Open `/admin` from a Telegram account whose ID is listed in `ADMIN_IDS`.

The panel shows user/activity/scan/payment statistics and lets an admin:
- find users by Telegram ID, username or name;
- grant 1/3/7/30 days manually, revoke access, ban/unban;
- edit subscription prices and enable/disable plans;
- view recent invoices/payments;
- switch access mode between `admin_only`, `subscription`, and `open`.

### Subscription payments

Two invoice providers are supported:
- CryptoBot / Crypto Pay: set `CRYPTO_PAY_TOKEN`;
- xRocket Pay: set `XROCKET_API_KEY`.

Invoices are created in USDT. The bot polls pending invoices every `PAYMENT_POLL_SECONDS` seconds, so a separate webhook web server is not required for this first commercial build. When a provider confirms `paid`, access is extended from the later of now or the user's current subscription end, and the user receives a Telegram notification.

The default plans are 1, 3, 7 and 30 days. Environment prices are only used when plans are created for the first time; afterwards change prices from `/admin` and they persist in the database.

### Safe rollout

Keep `ACCESS_MODE=admin_only` while testing. After both payment tokens are configured and a real test invoice has been checked, switch to `subscription` from `/admin`.

v3.2.2+ requires Railway PostgreSQL in production so user access, payments, scans and background history survive redeploys reliably.

## v3.2.3 — фильтр платных поднятий

- Добавлено распознавание Kleinanzeigen `bumpup` / **Hochschieben**.
- Учитываются варианты `featurelabel-bumpup`, `featuretag-bumpup`, `icon-feature-bumpup` и A/B-варианты в metadata.
- Расширена фильтрация Top-Anzeige / Highlight / Galerie.
- Обычные названия товаров со словами `Push Up` не считаются продвижением.


## v3.2.4 — hard Start/Stop
- Active scan cards now have a clear **⏹ Остановить парсер** action.
- `/stop` is available in the Telegram command menu.
- A stop detaches the user job immediately; if no other user shares that category scan, the underlying network/parser task is cancelled too.
- Cancelled scans are marked `cancelled` and do not create a result snapshot or automatic view-observation plan.
- After stopping, the bot offers **Выбрать категории** and **Запустить парсер** so a new run can start immediately.


## v3.3.3 — исправление навигации после CSV

- Исправлены кнопки главного меню, прикреплённые к документу результата.
- «Мои сканы», «Категории», «Настройки» и «Главное меню» теперь безопасно открываются и из CSV-сообщения.
- Если Telegram не позволяет редактировать документ через `edit_text`, бот автоматически отправляет новый текстовый экран.
- Основные переходы также очищают незавершённое FSM-состояние пользователя.


## v3.4.3 — Telegram BIGINT fix

- All persisted Telegram `user_id` values use PostgreSQL `BIGINT`.
- Existing PostgreSQL columns are migrated automatically on startup; no manual SQL is required.
- Fixes silent `/start` failures for newer Telegram accounts whose IDs exceed 32-bit `INTEGER`.
- `/start` now returns a visible temporary-database error instead of failing silently if profile persistence fails.

## v3.4.3 — 5-user concurrency & live progress

- Default scan workers raised from 4 to 5.
- Process-wide scan traffic can keep five category requests in flight while the shared rate limiter still smooths request starts.
- Main scan traffic receives four reserved global slots so inline view-count work cannot starve category-page requests.
- View enrichment is deliberately softened while 4+ scan jobs are active.
- Progress now moves during the date-location phase and shows real processed request count; users no longer see only an elapsed timer for several minutes.
- Existing 403/429 global cooldown/backoff remains enabled; the update does not bypass Kleinanzeigen limits.
