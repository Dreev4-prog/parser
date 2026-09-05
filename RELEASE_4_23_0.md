# DT Parser v4.23.0 — Vinted Radar 1.0 · Like Momentum

## Что изменилось

Vinted Radar больше не зависит от Exact Views/detail API. Основной сигнал спроса — публичный `catalog_favourite_count` и его изменение между повторными catalog snapshot.

### Live lifecycle

- AutoScan Radar: каждые 60 минут по сохранённым категориям.
- Каждый товар входит в Live с момента первого обнаружения нашим Radar.
- Первые 24 часа товар участвует в Live Score и сравнивается с товарами своего возрастного окна/категории.
- После 24 часов товар автоматически уходит из Live, но его первые 24 часа сохраняются в 7-дневном learning pool.
- Более позднее случайное присутствие старого товара в catalog scan не увеличивает его 0–24h learning evidence.

### Vinted Score 100

- ❤️ Like Velocity — 35
- 🚀 Like Acceleration — 15
- 💸 Price Edge — 20
- ❤️ Likes vs peers — 10
- 💎 Scarcity — 10
- 👤 Seller signal — 5
- 🔥 Brand momentum — 5

Первый замер — только baseline. HOT/RISING невозможны до подтверждённого положительного изменения likes. Counter regression считается невалидным интервалом, а не отрицательным спросом.

### Статусы

- 🔥 HOT
- 📈 RISING
- 💎 DEAL
- 👀 CANDIDATE
- baseline (внутренний)

### Производительность

Radar-круг теперь catalog-only. Он не ставит объявления в очередь Vinted Metrics Worker и не ждёт заблокированные detail endpoints. Manual Vinted Parser сохранил старый detail-metrics путь без изменений.

### Совместимость

- Kleinanzeigen parser/Radar не изменены.
- PostgreSQL schema не меняется.
- Новых Railway Variables нет.
- Существующие Vinted Scan Worker нужно redeploy вместе с Parser, потому что Radar-mode теперь пропускает detail metrics queue.
