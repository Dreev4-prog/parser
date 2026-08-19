# DT PARSER v4.3.30 — REGIONAL SHARD FIX

No Railway variable changes are required.

## What changed

- Keeps Cold Date Turbo from v4.3.29.
- Fixes regional `too_deep` shortcut: page 1 is locally verified before returning `too_deep`, so child location shards are available to `hidden_fill()`.
- If regional page 1 is weak/invalid, the shortcut is rejected and the proven local locator continues.
- Page Worker / View Worker / parser core are unchanged.

Recommended test: one category, oldest allowed date, 50 pages.
