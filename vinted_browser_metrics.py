from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from vinted_probe import VintedItem, VintedMetricSample, normalize_detail_metrics


DEFAULT_ENDPOINT_TEMPLATE = "/api/v2/items/{item_id}/details?localize=true"


def _utc_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _unknown(item_id: int, outcome: str, note: str, source: str = "browser_session") -> VintedMetricSample:
    return VintedMetricSample(
        item_id=item_id,
        source=source,
        measured_at=_utc_iso(),
        identity_ok=False,
        outcome=outcome,
        notes=[note],
    )


def _parse_session(raw: str) -> dict[str, Any]:
    if not raw.strip():
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {"cookies": payload}


def _playwright_cookie(entry: dict[str, Any], host: str) -> dict[str, Any] | None:
    name = str(entry.get("name") or "").strip()
    value = entry.get("value")
    if not name or value is None:
        return None
    domain = str(entry.get("domain") or host).strip() or host
    # Only first-party Vinted cookies belong in this context.  Never ingest arbitrary domains.
    if not (domain.lstrip(".").endswith("vinted.de") or domain.lstrip(".").endswith(host.lstrip("."))):
        return None
    result: dict[str, Any] = {
        "name": name,
        "value": str(value),
        "domain": domain,
        "path": str(entry.get("path") or "/") or "/",
        "httpOnly": bool(entry.get("httpOnly", False)),
        "secure": bool(entry.get("secure", True)),
    }
    expires = entry.get("expires")
    try:
        if expires is not None and float(expires) > 0:
            result["expires"] = float(expires)
    except Exception:
        pass
    same_site = str(entry.get("sameSite") or "").strip().lower()
    if same_site in {"strict", "lax", "none"}:
        result["sameSite"] = same_site.capitalize()
    return result


@dataclass(slots=True)
class BrowserMetricHealth:
    status: str = "starting"
    detail: str = ""
    blocked_streak: int = 0
    successes: int = 0
    failures: int = 0
    last_status_code: int | None = None
    circuit_until: float = 0.0


class _StartGate:
    """Space request starts without serialising the in-flight requests."""

    def __init__(self, interval: float) -> None:
        self.interval = max(0.0, float(interval))
        self._lock = asyncio.Lock()
        self._next = 0.0

    async def wait(self) -> None:
        if self.interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            delay = self._next - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._next = max(time.monotonic(), self._next) + self.interval


class VintedBrowserMetricClient:
    """Exact Vinted detail reader using a normal authenticated browser session.

    Important boundaries:
    - the worker never navigates to an item page while collecting metrics;
    - it only performs the same-origin JSON request from an already-open /catalog page;
    - no CAPTCHA solving, fingerprint spoofing, proxy rotation, or challenge bypass is attempted;
    - 401/403/429/challenge are fail-closed UNKNOWN and can open a circuit breaker.
    """

    def __init__(self, *, base_url: str, session_json: str, concurrency: int = 4, min_interval_seconds: float = 0.18) -> None:
        self.base_url = (base_url or "https://www.vinted.de").rstrip("/")
        self.session = _parse_session(session_json)
        self.concurrency = max(1, min(8, int(concurrency)))
        self.endpoint_template = str(self.session.get("metric_endpoint_template") or DEFAULT_ENDPOINT_TEMPLATE)
        if "{item_id}" not in self.endpoint_template:
            self.endpoint_template = DEFAULT_ENDPOINT_TEMPLATE
        self.user_agent = str(self.session.get("user_agent") or "").strip()
        self.locale = str(self.session.get("locale") or "de-DE").strip() or "de-DE"
        self.health = BrowserMetricHealth(status="session_missing" if not self.session else "starting")
        self._gate = _StartGate(min_interval_seconds)
        self._sem = asyncio.Semaphore(self.concurrency)
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None
        self._page: Page | None = None
        self._start_lock = asyncio.Lock()
        self._reload_lock = asyncio.Lock()


    def session_signature(self) -> str:
        import hashlib
        raw = json.dumps(self.session, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if self.session else ""
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16] if raw else ""

    async def reload_session(self, raw_session: str) -> str:
        """Replace the authenticated browser state without restarting Railway.

        Used by the admin-session service: Metrics Worker notices a newer DB session,
        closes only its isolated Vinted browser context and starts a fresh one.
        """
        async with self._reload_lock:
            parsed = _parse_session(raw_session)
            new_raw = json.dumps(parsed, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if parsed else ""
            old_raw = json.dumps(self.session, ensure_ascii=False, sort_keys=True, separators=(",", ":")) if self.session else ""
            if new_raw == old_raw and self._page is not None:
                return self.health.status
            await self.close()
            self.session = parsed
            self.endpoint_template = str(self.session.get("metric_endpoint_template") or DEFAULT_ENDPOINT_TEMPLATE)
            if "{item_id}" not in self.endpoint_template:
                self.endpoint_template = DEFAULT_ENDPOINT_TEMPLATE
            self.user_agent = str(self.session.get("user_agent") or "").strip()
            self.locale = str(self.session.get("locale") or "de-DE").strip() or "de-DE"
            self.health = BrowserMetricHealth(status="session_missing" if not self.session else "starting")
            return await self.start()

    @property
    def configured(self) -> bool:
        cookies = self.session.get("cookies")
        return isinstance(cookies, (list, dict)) and bool(cookies)

    async def start(self) -> str:
        if not self.configured:
            self.health.status = "session_missing"
            self.health.detail = "VINTED_SESSION_JSON is not configured"
            return self.health.status
        if self._page is not None:
            return self.health.status
        async with self._start_lock:
            if self._page is not None:
                return self.health.status
            try:
                self._pw = await async_playwright().start()
                self._browser = await self._pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                context_kwargs: dict[str, Any] = {"locale": self.locale}
                if self.user_agent:
                    context_kwargs["user_agent"] = self.user_agent
                self._context = await self._browser.new_context(**context_kwargs)
                host = urlparse(self.base_url).hostname or "www.vinted.de"
                cookies_raw = self.session.get("cookies")
                prepared: list[dict[str, Any]] = []
                if isinstance(cookies_raw, dict):
                    for name, value in cookies_raw.items():
                        prepared.append({"name": str(name), "value": str(value), "domain": host, "path": "/", "secure": True})
                elif isinstance(cookies_raw, list):
                    for raw in cookies_raw:
                        if isinstance(raw, dict):
                            cookie = _playwright_cookie(raw, host)
                            if cookie:
                                prepared.append(cookie)
                if prepared:
                    await self._context.add_cookies(prepared)
                self._page = await self._context.new_page()
                response = await self._page.goto(f"{self.base_url}/catalog", wait_until="domcontentloaded", timeout=30_000)
                status = response.status if response else None
                self.health.last_status_code = status
                page_text = (await self._page.content())[:20_000].lower()
                if status in {401, 403} or any(token in page_text for token in ("captcha", "datadome", "access denied", "challenge-platform")):
                    self.health.status = "challenge" if "captcha" in page_text or "datadome" in page_text else "blocked"
                    self.health.detail = f"catalog bootstrap HTTP {status or 'unknown'}"
                    return self.health.status
                self.health.status = "ready"
                self.health.detail = "authenticated browser context ready"
                return self.health.status
            except Exception as exc:
                self.health.status = "error"
                self.health.detail = f"{type(exc).__name__}: {exc}"[:240]
                await self.close()
                return self.health.status

    async def close(self) -> None:
        page, context, browser, pw = self._page, self._context, self._browser, self._pw
        self._page = None
        self._context = None
        self._browser = None
        self._pw = None
        for obj, method in ((page, "close"), (context, "close"), (browser, "close"), (pw, "stop")):
            if obj is None:
                continue
            try:
                await getattr(obj, method)()
            except Exception:
                pass

    def health_snapshot(self) -> dict[str, Any]:
        return {
            "provider": "browser-session",
            "provider_status": self.health.status,
            "provider_detail": self.health.detail[:160],
            "blocked_streak": self.health.blocked_streak,
            "provider_successes": self.health.successes,
            "provider_failures": self.health.failures,
            "provider_http": self.health.last_status_code,
            "provider_concurrency": self.concurrency,
            "endpoint": self.endpoint_template[:120],
        }

    async def _same_origin_fetch(self, path: str) -> dict[str, Any]:
        if self._page is None:
            return {"status": 0, "outcome": "provider_not_ready", "data": None, "preview": ""}
        return await self._page.evaluate(
            """
            async ({path}) => {
              try {
                const response = await fetch(path, {
                  method: 'GET',
                  credentials: 'include',
                  headers: {
                    'Accept': 'application/json, text/plain, */*',
                    'X-Requested-With': 'XMLHttpRequest'
                  }
                });
                const text = await response.text();
                let data = null;
                try { data = JSON.parse(text); } catch (_) {}
                return {status: response.status, data, preview: text.slice(0, 1200)};
              } catch (error) {
                return {status: 0, data: null, preview: String(error || 'fetch_error')};
              }
            }
            """,
            {"path": path},
        )

    def _register_failure(self, status: int, preview: str) -> str:
        self.health.failures += 1
        self.health.last_status_code = status or None
        lower = (preview or "").lower()
        if status == 401:
            outcome = "session_expired"
            self.health.status = "expired"
        elif status == 403:
            outcome = "challenge" if any(x in lower for x in ("captcha", "datadome", "challenge")) else "blocked"
            self.health.status = outcome
        elif status == 429:
            outcome = "rate_limited"
            self.health.status = outcome
        elif status == 404:
            outcome = "detail_not_found"
        elif status == 0:
            outcome = "browser_fetch_error"
        else:
            outcome = f"http_{status}"

        if outcome in {"blocked", "challenge", "session_expired", "rate_limited"}:
            self.health.blocked_streak += 1
        else:
            self.health.blocked_streak = 0
        if self.health.blocked_streak >= 5:
            self.health.circuit_until = time.monotonic() + 120.0
            self.health.status = "circuit_open"
            self.health.detail = f"5 consecutive protected responses; last={outcome}"
        return outcome

    async def fetch_item_detail(self, item: VintedItem) -> VintedMetricSample:
        if not self.configured:
            return _unknown(item.item_id, "session_missing", "Set VINTED_SESSION_JSON on Vinted Metrics Worker.")
        if self._page is None:
            await self.start()
        if self._page is None or self.health.status in {"error", "session_missing"}:
            return _unknown(item.item_id, "provider_unavailable", self.health.detail or self.health.status)
        if self.health.circuit_until > time.monotonic():
            return _unknown(item.item_id, "provider_circuit_open", self.health.detail or "protected endpoint circuit is open")
        if self.health.circuit_until and self.health.circuit_until <= time.monotonic():
            self.health.circuit_until = 0.0
            self.health.blocked_streak = 0
            self.health.status = "ready"

        path = self.endpoint_template.format(item_id=int(item.item_id))
        if path.startswith(self.base_url):
            path = path[len(self.base_url):] or "/"
        if not path.startswith("/"):
            path = "/" + path

        async with self._sem:
            await self._gate.wait()
            try:
                result = await self._same_origin_fetch(path)
            except Exception as exc:
                self.health.failures += 1
                self.health.status = "error"
                self.health.detail = f"{type(exc).__name__}: {exc}"[:240]
                return _unknown(item.item_id, "browser_fetch_error", self.health.detail)

        status = int(result.get("status") or 0)
        payload = result.get("data")
        preview = str(result.get("preview") or "")
        self.health.last_status_code = status or None
        if status != 200 or not isinstance(payload, dict):
            outcome = self._register_failure(status, preview)
            return _unknown(item.item_id, outcome, f"browser metric endpoint returned HTTP {status or 'transport'}")

        sample = normalize_detail_metrics(payload, expected_item_id=item.item_id)
        sample.source = "browser_session_detail"
        if sample.outcome == "wrong_identity":
            self.health.failures += 1
            self.health.status = "identity_error"
            self.health.detail = "detail payload item ID mismatch"
            return sample

        if sample.identity_ok:
            self.health.successes += 1
            self.health.blocked_streak = 0
            self.health.status = "ready"
            self.health.detail = "exact endpoint healthy"
            if sample.view_count is None:
                sample.outcome = "identity_ok_views_missing"
                sample.notes.append("Identity is exact but view_count is absent; remains UNKNOWN.")
            return sample

        self.health.failures += 1
        return sample
