# v4.23.3 GitHub patch

Apply on top of **v4.23.2**.

Main change: Vinted Radar validates the complete Vinted category tree but scans it as roughly **120 non-overlapping market segments** instead of thousands of terminal categories. Each segment keeps the same **15-page maximum** and the Telegram progress screen shows the real segment/page pass.

Redeploy **Parser/Bot** and all **Vinted Scan Worker** replicas after replacing the files. No SQL migration and no new Railway variables are required.
