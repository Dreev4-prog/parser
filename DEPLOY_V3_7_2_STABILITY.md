# DT PARSER v3.7.2 — Stability Core на Railway

## Что меняется

Основной production worker — `hybrid_worker.py`. В отличие от v3.7.1, он теперь не обязан запускать Chromium в начале каждого скана:

```text
HTTP-first
   │
   ├─ нормальный HTML → продолжаем лёгким HTTP
   │
   └─ transport/JS compatibility failure
            ↓
        Chromium fallback
            ↓
      storage_state → API HTTP
            ↓
        Chromium закрывается
```

Если Kleinanzeigen явно отвечает `403/429` или challenge, worker не меняет transport для обхода отказа — применяется обычный backoff и bounded recovery.

## Рекомендуемая схема Railway

```text
bot             ×1
PostgreSQL      ×1
Redis           ×1
hybrid-worker   ×5
views-worker    ×1
```

На время тестирования выключи старые `parser-worker` и `browser-worker`, чтобы несколько типов consumers не забирали одни и те же foreground jobs.

## hybrid-worker

Start Command:

```bash
python hybrid_worker.py
```

Подключи к сервису те же references:

```env
BOT_TOKEN=...
DATABASE_URL=${{Postgres.DATABASE_URL}}
REDIS_URL=${{Redis.REDIS_URL}}
DISTRIBUTED_WORKERS=1
```

Скрипт сам задаёт рекомендуемые defaults. Если эти Variables уже существуют в Railway, для первого теста используй:

```env
HYBRID_HTTP_FIRST=1
HYBRID_WATCHDOG_SECONDS=15
HYBRID_DIRECT_HTTP_RETRIES=1
HYBRID_HTTP_RETRIES=2
HYBRID_BROWSER_FALLBACK_LIMIT=3
HYBRID_CLOSE_BROWSER_AFTER_SEED=1
SCAN_AUTO_RECOVERY_PASSES=2
SCAN_AUTO_RECOVERY_DELAY_SECONDS=2
SCAN_PAGE_CHECKPOINT_TTL_SECONDS=900
HYBRID_SCAN_LANES=5
HYBRID_GLOBAL_LANES=8
```

## Что теперь происходит при partial

Первый проход не сразу превращается в пользовательскую «Допроверку». Если охват даты не подтверждён:

```text
основной проход
      ↓
verified pages → checkpoint
weak/missing pages → recovery
      ↓
recovery pass 1
      ↓
если нужно: recovery pass 2
      ↓
полный результат ИЛИ только тогда partial/manual recheck
```

Checkpoint живёт внутри parser-session конкретного UserScan. Он не является глобальным кэшем других пользователей и не меняет PostgreSQL.

## Что смотреть в Telegram

На «Поиск даты» теперь может появляться:

```text
⚡ HTTP-first
⏳ Ответ Kleinanzeigen: 7 сек · стр. 14
⚠️ Автоповторов по таймауту: 1
```

При неполном первом проходе:

```text
🔧 Автовосстановление скана
♻️ Попытка: 1/2
💾 Готовых страниц использовано: ...
```

Это позволяет отличить реальное ожидание внешнего ответа от зависшей очереди.

## Нагрузочный тест

Первый тест после deploy:

1. 5 Telegram-аккаунтов.
2. У каждого по 1 категории.
3. Глубина 25 страниц.
4. Одинаковая дата.
5. Запуск в течение 5–10 секунд.

Проверяем: у всех пяти меняется live status; запрос не остаётся молча на одной стадии; partial сначала проходит automatic recovery.

Если после v3.7.2 ровно часть workers систематически получает `403/429` при одновременном запуске, это уже показатель внешнего ограничения, а не очереди/Chromium. В таком случае увеличивать concurrency дальше не следует — надо уменьшать внешний request pressure/увеличивать reuse данных.
