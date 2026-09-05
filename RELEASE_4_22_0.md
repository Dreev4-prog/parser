# 4.22.0 — Vinted Parser Probe

**Base:** DT Parser 4.21.16 Radar 24H Category Handoff.

This release adds an isolated, admin/testing-only Vinted diagnostic worker without changing Kleinanzeigen parsing, Radar, Page Worker, Date Worker or View Worker behavior.

## What is included

- `Vinted Probe` / `DT_SERVICE_ROLE=vintedprobe` service target.
- HTTP-first Vinted catalog benchmark using `newest_first` and up to 96 items/page.
- Configurable catalog IDs, page count and conservative concurrency.
- Separate item-detail sampling with strict item-ID identity binding.
- `view_count` is accepted only when it is present on the exact requested item response.
- Missing/401/403/429/timeout/invalid JSON are **UNKNOWN/failure**, never synthetic zero.
- Catalog `view_count=0` is measured separately and is not automatically trusted as an exact view count.
- Report includes duplicate rate, promoted-field coverage, exact-view/favourite coverage, latency avg/p95 and HTTP outcomes.
- Quality gates: Catalog Access, Pagination Integrity, Identity, Chronology, Exact Views, short repeated-read View Stability and Radar Ready.
- Probe runs once by default and then idles instead of being restarted repeatedly by Railway.
- Optional compact Telegram report to `ADMIN_IDS`.

## Deliberate safety boundary

The probe does not solve CAPTCHA/DataDome/Cloudflare challenges, rotate proxies, spoof TLS fingerprints or retry 403/429 aggressively. If ordinary access is rejected, the benchmark says so and stops trusting the data.

## Railway test setup

Create **one** new service from the same repository:

- Service name: `Vinted Probe`
- Start command: existing `python service_launcher.py`
- `DT_SERVICE_ROLE=vintedprobe` is optional if the service is named `Vinted Probe`
- `BOT_TOKEN` + `ADMIN_IDS` optional (only for sending the result summary)
- `DATABASE_URL` and `REDIS_URL` are not required by this probe

Start with:

- `VINTED_PROBE_PAGES=3`
- `VINTED_PROBE_PAGE_CONCURRENCY=2`
- `VINTED_PROBE_DETAIL_SAMPLE=24`
- `VINTED_PROBE_DETAIL_CONCURRENCY=2`
- `VINTED_PROBE_STABILITY_READS=3`
- `VINTED_PROBE_REPEAT_MINUTES=0`

If the internal JSON API responds `401 invalid_authentication_token`, configure `VINTED_ACCESS_TOKEN_WEB` from your own normal Vinted browser session as a Railway secret. Do not paste session tokens into chat.

## Decision rule

Do **not** build Vinted Radar on top of this source until `radar_ready=PASS` or until a different authorized data source provides equivalent exact metric provenance.
