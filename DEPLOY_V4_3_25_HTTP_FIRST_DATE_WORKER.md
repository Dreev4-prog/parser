# DT PARSER v4.3.25 — HTTP-FIRST DATE WORKER

## Goal

Make Date Worker faster without reducing date accuracy.

## What changed

Date Worker now uses:

```text
HTTP probe
  -> strong chronology evidence: cache hint
  -> weak/ambiguous evidence: browser-confirm same page
  -> final boundary hint: main bot locally verifies with stable parser
```

HTTP results are accepted directly only when page identity is verified, the page is not suspicious, and there is enough exact date evidence. Empty/mixed pages, low date evidence and single-target hits are browser-confirmed first.

A 403/429 from Kleinanzeigen is treated as an explicit refusal and is not retried through Chromium. The normal local fallback remains available to the main bot.

## Railway

No new service is needed. Keep the existing `Date Worker` with 2 replicas and:

```text
REDIS_URL=${{Redis.REDIS_URL}}
```

No new variables are required. HTTP-first is enabled by default.

Optional emergency rollback for Date Worker only:

```text
DATE_WORKER_HTTP_FIRST=0
```

This restores browser-only remote probes without changing the main parser.

## Defaults

```text
DATE_WORKER_HTTP_FIRST=1
HYBRID_WATCHDOG_SECONDS=8
HYBRID_DIRECT_HTTP_RETRIES=1
DATE_WORKER_CONCURRENCY=2
DATE_CACHE_TTL_SECONDS=180
```

## Admin panel

`Admin -> 📅 Date Worker` now shows:

- HTTP fast probes
- browser confirmations
- HTTP/browser chronology conflicts
- 403/429
- probe speed

## Safe test

Run 3-4 categories for yesterday and 3-6 days ago. The useful signal is a growing `HTTP fast` counter while `browser confirm` stays much smaller. Every final boundary is still locally revalidated by the main bot.
