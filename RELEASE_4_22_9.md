# DT Parser v4.22.9 — Vinted Catalog Likes

## Что изменено

- `favourite_count` из публичного Vinted catalog становится отдельной рабочей метрикой `Catalog Likes`.
- Скан показывает покрытие лайков, число объявлений с лайками, суммарные лайки и максимум по одному товару.
- Карточка объявления использует `detail favourite_count`, если он реально подтверждён; иначе безопасно показывает `catalog_favourite_count` с явным источником.
- Для повторных сканов показывается Δ лайков по тому же `item_id` относительно предыдущего сохранённого скана.
- `UNKNOWN` не превращается в `0`; blocked detail endpoint не затирает catalog likes.
- Kleinanzeigen, Radar 3.x, Page/Date/View/Lifecycle workers и Vinted session flow не менялись.

## Зачем

Vinted catalog уже отдаёт `favourite_count` вместе с карточкой выдачи. Ранее UI показывал только `exact_favourites` из detail provider, который блокируется на Railway, поэтому визуально получался ноль. v4.22.9 разделяет источники и делает доступные likes видимыми без зависимости от exact views.
