# v4.23.4 GitHub patch

Apply on top of **v4.23.3**.

Main fix: opening **Vinted Lab / Vinted Radar** no longer performs the large seven-day scoring/likes work on the Telegram UI path. Radar scoring is single-flight, cached for 120 seconds by default, CPU scoring is moved off the asyncio event loop, Radar progress stops repeatedly aggregating the full item table, and scan watchers cannot keep overwriting another Vinted screen.

Replace the files from this archive preserving paths, then redeploy:

- Parser / Bot
- all Vinted Scan Worker replicas
- all Vinted Metrics Worker replicas

No SQL migration and no new Railway variables are required.
