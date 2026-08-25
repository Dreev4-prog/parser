# DT PARSER v4.11.2 — DT Radar Category Navigator

Base: v4.11.1 DT Radar AutoScan Error Recovery.

## What changed

DT Radar category navigation is now hierarchical instead of one flat list of every leaf category.

### Level 1 — large sections
`DT Radar -> Категории` shows the 15 main Kleinanzeigen sections in one list, for example:

- Auto, Rad & Boot
- Immobilien
- Haus & Garten
- Mode & Beauty
- Elektronik
- Haustiere
- Familie, Kind & Baby
- Jobs
- Freizeit, Hobby & Nachbarschaft
- Musik, Filme & Bücher
- Eintrittskarten & Tickets
- Dienstleistungen
- Verschenken & Tauschen
- Unterricht & Kurse
- Nachbarschaftshilfe

The number on the right is the total number of Radar products accumulated across that section's leaf categories.

### Level 2 — subcategories
Opening a large section shows only its real leaf subcategories. For example `Elektronik` shows Handy & Telefon, Haushaltsgeräte, Audio & Hifi, Foto, Konsolen, Laptops & Notebooks, PCs, PC-Zubehör & Software, Tablets & Reader, TV & Video, Videospiele, Wearables, etc.

Selecting a subcategory opens the existing Radar product list. DT Score, Peak Score and all product analytics are unchanged.

### Back navigation
- product list -> returns to its parent large section
- parent section -> returns to all large sections
- all large sections -> returns to DT Radar home

## Unchanged

- DT Score and product ranking
- DT Radar database and product history
- AutoScan 141 leaf categories / 15 pages
- AutoScan Error Recovery / retry only failures
- Page Cache Recovery
- View / Date / Page / AI worker behavior
- four foreground user parser lanes

## Railway

No new variables or migrations are required.

Only the main `parser` service needs redeployment. Date/Page/View/AI workers are unchanged.

## Smoke test

1. Open `DT Radar -> Категории`.
2. Verify a single list of 15 large sections is shown.
3. Open `Elektronik`.
4. Verify only Elektronik subcategories are shown.
5. Open `Konsolen` (or another subcategory).
6. Verify Radar products still show DT Score.
7. Press Back and verify it returns to Elektronik, not the full 141-category list.
8. Verify Admin -> Radar AutoScan still works normally.
