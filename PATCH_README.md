# v4.23.1 GitHub patch

Накладывать **поверх v4.23.0**.

Заменить/добавить файлы из архива с сохранением путей. После push/redeploy обязательны:

- Parser / Bot
- все Vinted Scan Worker

Vinted Metrics Worker и Vinted Session Worker функционально этим патчем не менялись.

Новых Railway variables и ручных SQL-миграций нет.

После deploy старый Radar-конфиг с выбранными категориями больше не используется для новых due-rounds. Для чистого старта можно нажать «Остановить Radar AutoScan» и затем «▶️ Запустить Radar · весь Vinted».
