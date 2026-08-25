# DT PARSER v4.10.2 — Page Cache Recovery

Built directly on v4.10.1. Full DT Radar is preserved.

## Root cause found in production

A Page Worker-prefetched Redis page could pass page identity/date checks yet have the same listing fingerprint as a previously consumed page. v4.10.1 detected `repeated-content`, but `stable_fetch()` only cleared the local in-process cache. The next retry could therefore read the exact same poisoned Redis value again. BrowserContext reset could not help because the retry never reached the local browser.

## Fix

- Detect repeated fingerprint from a remote Page Worker response.
- Immediately delete the corresponding Redis page cache/pending key.
- Pin that requested page to the local stable parser for the rest of the category locator.
- Normal retries and BrowserContext reset now operate on genuinely fresh local content.
- If the local page itself still repeats, the existing bounded repeated-content recovery remains in force.
- Do not raise the repeat-skip limit: repeated pages are never counted as verified depth.

## Unchanged

- DT Radar and DT AI
- four foreground parser lanes / fifth+ FIFO
- free trials and payments
- Date Worker
- View Speed sharding from v4.10.1
- strict challenge/page-identity handling

## Expected production log

When remote cache poisoning is detected:

```text
Repeated Page Worker cache invalidated category=... page=4 previous=3; forcing local retry
```

The following retry should either become a normal `relation=target` local page or, only if Kleinanzeigen itself still repeats the page, proceed through the bounded repeat recovery.
