# Kleinanzeigen Parser Bot v2.6.7 — View Source Inspector

This diagnostic release keeps the v2.6.6 parser and adds a network inspector to the existing **👁 Тест просмотров** flow.

- Reads the public counter from `#viewad-cntr-num`.
- Captures only public XHR/fetch response URLs and small text snippets.
- Searches for likely `views/viewCount/counter/Aufruf` sources and the current counter value.
- Never exposes cookies, request headers or authentication data.
- If no direct source is found, the bot explicitly reports that Chromium/DOM remains the reliable method.

Mass parsing behavior from v2.6.6 is unchanged in this diagnostic build.

Railway start command: `python bot.py`.
