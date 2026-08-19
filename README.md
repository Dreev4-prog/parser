# DT PARSER v4.3.24 — DATE WORKER PRO

This release adds the third independent acceleration layer:

```text
Date Worker x2 -> Page Worker x2 -> View Worker x2
```

Date Worker parallelizes chronology probes and uses a 180-second Redis cache/single-flight layer. Its output is never treated as final truth: the stable main parser locally verifies the boundary before collection starts. If remote date search is unavailable or inconsistent, the known-good local date locator is used automatically.

Date selection is now limited to the last 7 calendar dates (today + previous 6 days), with all seven dates shown directly in the Telegram picker.

The v4.3.23 parser core, Page Worker safety gate, View Worker, View Sharding, exact view extraction and traffic core remain unchanged.

See `DEPLOY_V4_3_24_DATE_WORKER_PRO.md`.
