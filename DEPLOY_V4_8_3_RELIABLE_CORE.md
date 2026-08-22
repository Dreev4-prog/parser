# DT PARSER v4.8.3 — Reliable Core

Основа: v4.8.2 / Golden 4.4 parsing lineage. Релиз меняет только P0-участки, которые создавали искусственные паузы или плохое распределение задач.

## P0 изменения

1. **Bot/local fallback = resilient traffic**
   - 403: hard-pause 0 сек;
   - 429: локальная пауза до ~3 сек;
   - penalty максимум 1;
   - восстановление после 10 успешных запросов / ~10 сек.

2. **Fast-fail одной страницы**
   - category HTTP и browser transport делают максимум один короткий retry после 403/429;
   - старые request-local recovery windows 45–180 сек больше не удерживают scan lane;
   - после повторного отказа вызывающий код получает `TemporaryAccessError` и может продолжить recovery/fallback без блокировки всей очереди.

3. **Release-scoped Redis runtime**
   - Date jobs/pending/error/heartbeat: `dtparser:dateworker:runtime:v483:*`;
   - Page jobs/pending/error/heartbeat: `dtparser:pageworker:runtime:v483:*`;
   - View jobs/progress/result/worker heartbeat: `dtparser:viewcounter:runtime:v483:*`;
   - Date cache/predictor и Page cache остаются на стабильных старых ключах и не теряются.
   - Старые runtime jobs v4.8.2/4.8.1 больше не могут быть reclaimed новым worker fleet. Ручная очистка Redis при этом релизе не нужна.

4. **Fresh jobs first**
   - Date/Page/View сначала читают новые stream jobs;
   - `XAUTOCLAIM` crash-recovery выполняется только когда свежая очередь пуста.

5. **Page identity без жёстких 25 слотов**
   - `/seite:N` в final URL является главным подтверждением номера страницы;
   - result stride выводится из фактического offset, когда это возможно;
   - изменение количества organic slots больше не должно создавать массовое `verified=True matches=False` на корректных страницах;
   - redirect на другую `/seite:M` всё ещё отклоняется.

6. **Быстрый Page handoff**
   - `PAGE_CACHE_WAIT_MS=450` вместо 1800;
   - polling 75 мс;
   - если remote Page Worker не успел, foreground быстрее продолжает fallback вместо ожидания почти 2 секунд на каждой странице.

7. **View sharding для обычного скана**
   - sharding начинается с 40 URL;
   - target shard ~18 URL;
   - ожидаемый fleet = 4 View Worker;
   - типичный batch 50–60 объявлений делится примерно на 4 независимых shard, поэтому четыре реплики реально работают на один пользовательский scan.

## Что намеренно не менялось

- Stable Engine и финальная проверка границы даты;
- фильтры и dedupe;
- extraction объявлений;
- алгоритм точного view counter;
- RU/EN, админка, AI Lab, Product Opportunity Engine.

## Деплой

Все сервисы, которые используют runtime queues, должны быть на одном релизе **4.8.3 одновременно**:

- parser/bot;
- Date Worker;
- Page Worker;
- View Worker.

Из-за нового `runtime:v483` старый v4.8.2 worker не увидит новые jobs от v4.8.3 bot и наоборот. На Railway после обновления репозитория дождись, пока все четыре сервиса покажут новый launcher log `version=4.8.3` и workers снова будут 4/4.

Ручные Variables `DIST_TRAFFIC_SHARED_COOLDOWN=0` не нужны: профиль встроен в код.

## Первый тест

1. Один scan, 50 страниц, та же категория/дата, которую использовали для A/B.
2. Отдельно записать время Date / Pages / Views.
3. В логах проверить:
   - после 403 нет пауз 15–30 сек;
   - Page Worker перестал массово писать `verified=True matches=False` на корректных `/seite:N`;
   - View Manager пишет sharding для 50–60 URL примерно на 4 shard;
   - четыре View Worker получают разные `View job admitted`.
4. После успешного одиночного теста запустить два scan одновременно.

## Rollback

Rollback на v4.8.2 безопасен: его старые Redis namespaces не были удалены. После rollback v4.8.2 снова будет использовать свои старые runtime keys, а `runtime:v483` со временем истечёт по TTL.
