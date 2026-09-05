# DT Parser v4.22.9 — Vinted Catalog Likes

Накладывать поверх v4.22.8.

## Что делает патч
- Поднимает `favourite_count` из Vinted catalog в отдельную рабочую метрику `Catalog Likes`.
- Показывает покрытие, число товаров с лайками, сумму лайков и максимум.
- В карточке товара показывает likes с источником `catalog` или `detail`.
- Для повторного скана показывает Δ likes по тому же `item_id` относительно предыдущего сохранённого скана.
- `UNKNOWN` никогда не превращается в `0`.
- Не меняет Kleinanzeigen, Radar 3.x, Page/Date/View/Lifecycle workers или Vinted session flow.

## Деплой
Заменить/добавить файлы из архива в корне репозитория, затем redeploy Parser. Vinted Scan Worker можно redeploy для одинаковой версии, но логика его каталога не менялась.

Новых Railway Variables и SQL миграций нет.
