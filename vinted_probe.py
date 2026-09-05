from __future__ import annotations

import asyncio
import json
import os
import re
import statistics
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

import httpx


DEFAULT_BASE_URL = "https://www.vinted.de"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0 Safari/537.36"
)
MOBILE_USER_AGENT = "vinted-ios Vinted/22.6.1 (lt.manodrabuziai.fr; build:21794; iOS 15.2.0) iPhone10,6"
MOBILE_APP_VERSION = "22.6.1"
MOBILE_DEVICE_MODEL = "iPhone10,6"
DEFAULT_PROBE_CATALOG_IDS = (4, 5)  # realistic category-specific probe: women/men clothing


class VintedProbeError(RuntimeError):
    pass


class VintedAccessError(VintedProbeError):
    pass


@dataclass(slots=True)
class ProbeHTTPRecord:
    kind: str
    url: str
    status_code: int | None
    elapsed_ms: int
    outcome: str
    content_type: str = ""
    detail: str = ""


@dataclass(slots=True)
class VintedItem:
    item_id: int
    title: str = ""
    url: str = ""
    price_amount: float | None = None
    currency: str = ""
    brand: str = ""
    size: str = ""
    condition: str = ""
    seller_id: int | None = None
    seller_login: str = ""
    catalog_id: int | None = None
    promoted: bool | None = None
    visible: bool | None = None
    catalog_view_count: int | None = None
    catalog_favourite_count: int | None = None
    upload_raw: str | int | float | None = None


@dataclass(slots=True)
class VintedMetricSample:
    item_id: int
    source: str
    measured_at: str
    view_count: int | None = None
    favourite_count: int | None = None
    upload_raw: str | int | float | None = None
    sold: bool | None = None
    closed: bool | None = None
    reserved: bool | None = None
    hidden: bool | None = None
    visible: bool | None = None
    identity_ok: bool = False
    outcome: str = "unknown"
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class VintedProbeConfig:
    base_url: str = DEFAULT_BASE_URL
    catalog_ids: tuple[int, ...] = ()
    pages: int = 3
    per_page: int = 96
    page_concurrency: int = 2
    detail_sample: int = 24
    detail_concurrency: int = 2
    request_timeout_seconds: float = 15.0
    min_interval_seconds: float = 0.35
    access_token_web: str = ""
    session_json: str = ""
    report_path: str = ""
    stability_reads: int = 3
    stability_delay_seconds: float = 1.2
    recovery_pages: int = 3

    @classmethod
    def from_env(cls) -> "VintedProbeConfig":
        raw_catalogs = os.getenv("VINTED_PROBE_CATALOG_IDS", "").strip()
        catalog_ids: list[int] = []
        for chunk in raw_catalogs.split(","):
            value = chunk.strip()
            if value.isdigit():
                catalog_ids.append(int(value))
        if not catalog_ids:
            catalog_ids = list(DEFAULT_PROBE_CATALOG_IDS)
        return cls(
            base_url=os.getenv("VINTED_BASE_URL", DEFAULT_BASE_URL).strip().rstrip("/") or DEFAULT_BASE_URL,
            catalog_ids=tuple(catalog_ids),
            pages=max(1, min(25, int(os.getenv("VINTED_PROBE_PAGES", "3")))),
            per_page=max(1, min(96, int(os.getenv("VINTED_PROBE_PER_PAGE", "96")))),
            page_concurrency=max(1, min(8, int(os.getenv("VINTED_PROBE_PAGE_CONCURRENCY", "2")))),
            detail_sample=max(0, min(200, int(os.getenv("VINTED_PROBE_DETAIL_SAMPLE", "24")))),
            detail_concurrency=max(1, min(8, int(os.getenv("VINTED_PROBE_DETAIL_CONCURRENCY", "2")))),
            request_timeout_seconds=max(5.0, min(60.0, float(os.getenv("VINTED_PROBE_TIMEOUT_SECONDS", "15")))),
            min_interval_seconds=max(0.0, min(5.0, float(os.getenv("VINTED_PROBE_MIN_INTERVAL_SECONDS", "0.35")))),
            access_token_web=os.getenv("VINTED_ACCESS_TOKEN_WEB", "").strip(),
            session_json=os.getenv("VINTED_SESSION_JSON", "").strip(),
            report_path=os.getenv("VINTED_PROBE_REPORT_PATH", "").strip(),
            stability_reads=max(0, min(5, int(os.getenv("VINTED_PROBE_STABILITY_READS", "3")))),
            stability_delay_seconds=max(0.2, min(10.0, float(os.getenv("VINTED_PROBE_STABILITY_DELAY_SECONDS", "1.2")))),
            recovery_pages=max(0, min(10, int(os.getenv("VINTED_PROBE_RECOVERY_PAGES", "3")))),
        )


class _RateGate:
    def __init__(self, interval: float) -> None:
        self.interval = max(0.0, float(interval))
        self._lock = asyncio.Lock()
        self._next_at = 0.0

    async def wait(self) -> None:
        if self.interval <= 0:
            return
        async with self._lock:
            now = time.monotonic()
            delay = self._next_at - now
            if delay > 0:
                await asyncio.sleep(delay)
            self._next_at = time.monotonic() + self.interval


class VintedProbeClient:
    """Small, fail-closed Vinted diagnostic client.

    This client deliberately does not solve anti-bot challenges, rotate proxies,
    spoof TLS fingerprints, or retry 403/429 aggressively. The goal is to measure
    what a normal isolated worker can read reliably before DT Radar trusts it.
    """

    def __init__(self, config: VintedProbeConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.config = config
        self.records: list[ProbeHTTPRecord] = []
        self._gate = _RateGate(config.min_interval_seconds)
        headers = {
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
            "User-Agent": DEFAULT_USER_AGENT,
            "Referer": f"{config.base_url}/",
            "X-Requested-With": "XMLHttpRequest",
        }
        self.client = httpx.AsyncClient(
            headers=headers,
            follow_redirects=True,
            timeout=httpx.Timeout(config.request_timeout_seconds),
            transport=transport,
        )
        host = urlparse(config.base_url).hostname or "www.vinted.de"
        token = config.access_token_web.strip()
        if token:
            self.client.cookies.set("access_token_web", token, domain=host, path="/")
        self.session_cookie_names: list[str] = []
        raw_session = config.session_json.strip()
        if raw_session:
            try:
                payload = json.loads(raw_session)
                cookies: Any = payload.get("cookies") if isinstance(payload, dict) and "cookies" in payload else payload
                if isinstance(cookies, dict):
                    for name, value in cookies.items():
                        if not isinstance(name, str) or value is None:
                            continue
                        self.client.cookies.set(name, str(value), domain=host, path="/")
                        self.session_cookie_names.append(name)
                elif isinstance(cookies, list):
                    for entry in cookies:
                        if not isinstance(entry, dict):
                            continue
                        name = str(entry.get("name") or "").strip()
                        value = entry.get("value")
                        if not name or value is None:
                            continue
                        domain = str(entry.get("domain") or host).strip() or host
                        path = str(entry.get("path") or "/").strip() or "/"
                        self.client.cookies.set(name, str(value), domain=domain, path=path)
                        self.session_cookie_names.append(name)
            except Exception:
                # Auth configuration is optional. An invalid secret must never crash
                # catalog collection; exact metrics simply remain fail-closed UNKNOWN.
                self.session_cookie_names = []
        self.session_cookie_names = sorted(set(self.session_cookie_names))
        self._public_oauth_token: str = ""
        self._public_oauth_attempted = False
        self.bootstrap_cookie_names: list[str] = []
        self.oauth_outcome: str = "not_attempted"

    async def __aenter__(self) -> "VintedProbeClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.client.aclose()

    @staticmethod
    def _looks_like_challenge(response: httpx.Response) -> bool:
        text = response.text[:5000].lower()
        return any(token in text for token in ("datadome", "captcha", "access denied", "challenge-platform", "cf-chl"))

    @staticmethod
    def _outcome_for(response: httpx.Response) -> str:
        if response.status_code == 401:
            return "authentication_required"
        if response.status_code == 403:
            return "blocked"
        if response.status_code == 429:
            return "rate_limited"
        if response.status_code >= 500:
            return "server_error"
        if VintedProbeClient._looks_like_challenge(response):
            return "challenge"
        if 200 <= response.status_code < 300:
            return "ok"
        if response.status_code == 404:
            return "not_found"
        return f"http_{response.status_code}"

    async def _request(self, kind: str, url: str, *, params: dict[str, Any] | None = None, expect_json: bool = True, headers: dict[str, str] | None = None) -> tuple[httpx.Response | None, Any | None, str]:
        await self._gate.wait()
        started = time.perf_counter()
        try:
            response = await self.client.get(url, params=params, headers=headers)
        except httpx.HTTPError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            self.records.append(ProbeHTTPRecord(kind, url, None, elapsed, "transport_error", detail=type(exc).__name__))
            return None, None, "transport_error"

        elapsed = int((time.perf_counter() - started) * 1000)
        outcome = self._outcome_for(response)
        content_type = response.headers.get("content-type", "")
        self.records.append(ProbeHTTPRecord(kind, str(response.url), response.status_code, elapsed, outcome, content_type=content_type))

        if outcome != "ok":
            return response, None, outcome
        if not expect_json:
            return response, response.text, outcome
        try:
            payload = response.json()
        except ValueError:
            self.records[-1].outcome = "invalid_json"
            return response, None, "invalid_json"
        if isinstance(payload, dict) and int(payload.get("code") or 0) == 106:
            self.records[-1].outcome = "rate_limited"
            return response, payload, "rate_limited"
        return response, payload, outcome

    async def _post_json(self, kind: str, url: str, *, payload: dict[str, Any], headers: dict[str, str] | None = None) -> tuple[httpx.Response | None, Any | None, str]:
        await self._gate.wait()
        started = time.perf_counter()
        try:
            response = await self.client.post(url, json=payload, headers=headers)
        except httpx.HTTPError as exc:
            elapsed = int((time.perf_counter() - started) * 1000)
            self.records.append(ProbeHTTPRecord(kind, url, None, elapsed, "transport_error", detail=type(exc).__name__))
            return None, None, "transport_error"
        elapsed = int((time.perf_counter() - started) * 1000)
        outcome = self._outcome_for(response)
        content_type = response.headers.get("content-type", "")
        self.records.append(ProbeHTTPRecord(kind, str(response.url), response.status_code, elapsed, outcome, content_type=content_type))
        if outcome != "ok":
            return response, None, outcome
        try:
            return response, response.json(), outcome
        except ValueError:
            self.records[-1].outcome = "invalid_json"
            return response, None, "invalid_json"

    async def _ensure_public_oauth_token(self) -> str:
        if self._public_oauth_attempted:
            return self.oauth_outcome
        self._public_oauth_attempted = True
        _response, payload, outcome = await self._post_json(
            "oauth_public",
            f"{self.config.base_url}/oauth/token",
            payload={"grant_type": "password", "client_id": "ios", "scope": "public"},
            headers={"User-Agent": MOBILE_USER_AGENT, "Accept": "application/json"},
        )
        if outcome == "ok" and isinstance(payload, dict) and isinstance(payload.get("access_token"), str):
            self._public_oauth_token = payload["access_token"]
            self.oauth_outcome = "ok"
        else:
            self.oauth_outcome = outcome
        return self.oauth_outcome

    async def bootstrap(self) -> str:
        # `/catalog` is the normal browsing entry used by current Vinted clients to
        # establish an anonymous web session. We record cookie NAMES only; values
        # are never logged. If the session is challenged, the probe fails closed.
        response, _text, outcome = await self._request("bootstrap", f"{self.config.base_url}/catalog", expect_json=False)
        self.bootstrap_cookie_names = sorted({cookie.name for cookie in self.client.cookies.jar})
        if response is None:
            return outcome
        return outcome

    async def fetch_catalog_page(self, catalog_id: int | None, page: int) -> tuple[list[VintedItem], str, int | float | str | None]:
        """Read one newest-first catalog page.

        `pagination.time` is telemetry/cache-buster data, not a proven snapshot
        cursor. v4.22.2 therefore uses a fresh request time and recovers unique
        depth by continuing to deeper pages when a live feed shifts underneath us.
        """
        params: dict[str, Any] = {
            "page": page,
            "per_page": self.config.per_page,
            "order": "newest_first",
            "time": time.time(),
        }
        if catalog_id is not None:
            params["catalog_ids"] = catalog_id
        _response, payload, outcome = await self._request(
            "catalog",
            f"{self.config.base_url}/api/v2/catalog/items",
            params=params,
        )
        if outcome != "ok" or not isinstance(payload, dict):
            return [], outcome, None
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            return [], "invalid_catalog_shape", None
        pagination = payload.get("pagination") if isinstance(payload.get("pagination"), dict) else {}
        response_time = pagination.get("time") if pagination else None
        items: list[VintedItem] = []
        for raw in raw_items:
            item = normalize_catalog_item(raw, self.config.base_url)
            if item is not None:
                items.append(item)
        return items, "ok", response_time

    async def fetch_item_detail(self, item: VintedItem) -> VintedMetricSample:
        """Fetch exact metrics without trusting public-page views.

        Order:
        1) authenticated-by-session web item endpoint `/api/v2/items/{id}`;
        2) browser detail endpoint `/api/v2/items/{id}/details`;
        3) read-only public OAuth token + mobile item endpoint.

        The public HTML page is intentionally NOT used for exact views in v4.22.3,
        because ordinary item-page GETs may themselves affect Vinted's view counter.
        Missing values stay UNKNOWN.
        """
        base_headers = {
            "Accept": "application/json, text/plain, */*",
            "Referer": item.url or f"{self.config.base_url}/catalog",
            "X-Requested-With": "XMLHttpRequest",
        }
        attempts: list[tuple[str, str, dict[str, str], dict[str, Any]]] = [
            ("detail_web_item", f"{self.config.base_url}/api/v2/items/{item.item_id}", base_headers, {"localize": "true"}),
            ("detail_web_details", f"{self.config.base_url}/api/v2/items/{item.item_id}/details", base_headers, {"localize": "true"}),
        ]
        best: VintedMetricSample | None = None
        for kind, url, headers, params in attempts:
            _response, payload, outcome = await self._request(kind, url, params=params, headers=headers)
            if outcome != "ok" or not isinstance(payload, dict):
                continue
            sample = normalize_detail_metrics(payload, expected_item_id=item.item_id)
            sample.source = kind
            if sample.outcome == "wrong_identity":
                return sample
            if sample.identity_ok:
                best = _merge_metric_samples(best, sample)
                if best.view_count is not None and best.upload_raw is not None:
                    return best

        oauth_outcome = await self._ensure_public_oauth_token()
        if oauth_outcome == "ok" and self._public_oauth_token:
            mobile_headers = {
                "Authorization": f"Bearer {self._public_oauth_token}",
                "User-Agent": MOBILE_USER_AGENT,
                "x-app-version": MOBILE_APP_VERSION,
                "x-device-model": MOBILE_DEVICE_MODEL,
                "short-bundle-version": MOBILE_APP_VERSION,
                "Accept": "application/json",
            }
            for kind, path in (
                ("detail_mobile_item", f"/api/v2/items/{item.item_id}"),
                ("detail_mobile_details", f"/api/v2/items/{item.item_id}/details"),
            ):
                _response, payload, outcome = await self._request(
                    kind, f"{self.config.base_url}{path}", params={"localize": "true"}, headers=mobile_headers
                )
                if outcome != "ok" or not isinstance(payload, dict):
                    continue
                sample = normalize_detail_metrics(payload, expected_item_id=item.item_id)
                sample.source = kind
                if sample.outcome == "wrong_identity":
                    return sample
                if sample.identity_ok:
                    best = _merge_metric_samples(best, sample)
                    if best.view_count is not None and best.upload_raw is not None:
                        return best

        if best is not None:
            if best.view_count is None:
                best.notes.append("exact view_count unavailable from web/mobile APIs; remains UNKNOWN")
            if best.upload_raw is None:
                best.notes.append("upload chronology unavailable from web/mobile APIs")
            return best
        return VintedMetricSample(
            item_id=item.item_id,
            source="web_api+mobile_oauth",
            measured_at=_utcnow_iso(),
            identity_ok=False,
            outcome="exact_detail_unavailable",
            notes=[
                "No identity-bound exact detail API succeeded.",
                f"public_oauth={oauth_outcome}",
                "Public HTML is not used for exact views because it may affect the view counter.",
            ],
        )


def _merge_metric_samples(base: VintedMetricSample | None, incoming: VintedMetricSample) -> VintedMetricSample:
    if base is None:
        return incoming
    for field_name in ("view_count", "favourite_count", "upload_raw", "sold", "closed", "reserved", "hidden", "visible"):
        if getattr(base, field_name) is None and getattr(incoming, field_name) is not None:
            setattr(base, field_name, getattr(incoming, field_name))
    if incoming.source not in base.source:
        base.source = f"{base.source}+{incoming.source}"
    for note in incoming.notes:
        if note not in base.notes:
            base.notes.append(note)
    base.identity_ok = base.identity_ok or incoming.identity_ok
    return base

def _utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _int_or_none(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _float_or_none(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _nested(data: dict[str, Any], *path: str) -> Any:
    current: Any = data
    for key in path:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _first(data: dict[str, Any], keys: Iterable[str]) -> Any:
    for key in keys:
        if key in data and data.get(key) is not None:
            return data.get(key)
    return None


def normalize_catalog_item(raw: Any, base_url: str = DEFAULT_BASE_URL) -> VintedItem | None:
    if not isinstance(raw, dict):
        return None
    item_id = _int_or_none(raw.get("id"))
    if item_id is None or item_id <= 0:
        return None
    price_amount = _float_or_none(_nested(raw, "price", "amount"))
    currency = str(_nested(raw, "price", "currency_code") or raw.get("currency") or "")
    user = raw.get("user") if isinstance(raw.get("user"), dict) else {}
    url = str(raw.get("url") or "")
    if not url:
        path = str(raw.get("path") or "")
        if path.startswith("/"):
            url = f"{base_url}{path}"
        else:
            url = f"{base_url}/items/{item_id}"
    promoted_raw = _first(raw, ("promoted", "is_promoted", "is_promoted_item"))
    promoted = bool(promoted_raw) if promoted_raw is not None else None
    visible_raw = _first(raw, ("is_visible", "visible"))
    visible = bool(visible_raw) if visible_raw is not None else None
    return VintedItem(
        item_id=item_id,
        title=str(raw.get("title") or ""),
        url=url,
        price_amount=price_amount,
        currency=currency,
        brand=str(raw.get("brand_title") or _nested(raw, "brand", "title") or ""),
        size=str(raw.get("size_title") or ""),
        condition=str(raw.get("status") or raw.get("status_title") or ""),
        seller_id=_int_or_none(user.get("id")),
        seller_login=str(user.get("login") or ""),
        catalog_id=(
            _int_or_none(_first(raw, ("catalog_id", "catalogId")))
            or _int_or_none(_nested(raw, "catalog", "id"))
        ),
        promoted=promoted,
        visible=visible,
        catalog_view_count=_int_or_none(_first(raw, ("view_count", "views_count", "views"))),
        catalog_favourite_count=_int_or_none(_first(raw, ("favourite_count", "favorites_count", "favourites_count"))),
        upload_raw=_first(raw, ("created_at", "created_at_ts", "upload_date", "uploaded_at")),
    )


def normalize_detail_metrics(payload: dict[str, Any], *, expected_item_id: int) -> VintedMetricSample:
    raw = payload.get("item") if isinstance(payload.get("item"), dict) else payload
    if not isinstance(raw, dict):
        return VintedMetricSample(
            item_id=expected_item_id,
            source="detail_api",
            measured_at=_utcnow_iso(),
            identity_ok=False,
            outcome="invalid_detail_shape",
        )
    actual_id = _int_or_none(raw.get("id"))
    identity_ok = actual_id == expected_item_id
    notes: list[str] = []
    if not identity_ok:
        notes.append(f"identity mismatch: expected={expected_item_id} actual={actual_id}")
    view_count = _int_or_none(_first(raw, ("view_count", "views_count", "views")))
    favourite_count = _int_or_none(_first(raw, ("favourite_count", "favorites_count", "favourites_count")))
    if view_count is None:
        notes.append("view_count absent: exact views remain UNKNOWN")
    if favourite_count is None:
        notes.append("favourite_count absent")
    return VintedMetricSample(
        item_id=expected_item_id,
        source="detail_api",
        measured_at=_utcnow_iso(),
        view_count=view_count if identity_ok else None,
        favourite_count=favourite_count if identity_ok else None,
        upload_raw=_first(raw, ("created_at", "created_at_ts", "upload_date", "uploaded_at")),
        sold=_bool_or_none(_first(raw, ("is_sold", "sold"))),
        closed=_bool_or_none(_first(raw, ("is_closed", "closed"))),
        reserved=_bool_or_none(_first(raw, ("is_reserved", "reserved"))),
        hidden=_bool_or_none(_first(raw, ("is_hidden", "hidden"))),
        visible=_bool_or_none(_first(raw, ("is_visible", "visible"))),
        identity_ok=identity_ok,
        outcome="ok" if identity_ok else "wrong_identity",
        notes=notes,
    )



def _next_flight_blob(html: str) -> str:
    """Decode string chunks emitted by Next.js `self.__next_f.push` calls."""
    parts: list[str] = []
    pattern = re.compile(r'self\.__next_f\.push\(\[\d+\s*,\s*("(?:[^"\\]|\\.)*")\s*\]\)', re.S)
    for match in pattern.finditer(html):
        try:
            decoded = json.loads(match.group(1))
        except (ValueError, TypeError):
            continue
        if isinstance(decoded, str):
            parts.append(decoded)
    return "\n".join(parts)


def _json_scalar_near(text: str, start: int, keys: tuple[str, ...], *, radius: int = 50000) -> Any:
    left = max(0, start - radius // 4)
    right = min(len(text), start + radius)
    window = text[left:right]
    for key in keys:
        # Handle ordinary decoded JSON and still-escaped JSON strings.
        patterns = (
            rf'"{re.escape(key)}"\s*:\s*(null|true|false|-?\d+(?:\.\d+)?|"(?:[^"\\]|\\.)*")',
            rf'\\"{re.escape(key)}\\"\s*:\s*(null|true|false|-?\d+(?:\.\d+)?|\\"(?:[^"\\]|\\.)*\\")',
        )
        for pattern in patterns:
            m = re.search(pattern, window, re.S)
            if not m:
                continue
            raw = m.group(1)
            try:
                if raw.startswith('\\"') and raw.endswith('\\"'):
                    raw = '"' + raw[2:-2].replace('\\"', '"') + '"'
                return json.loads(raw)
            except Exception:
                if raw == "null":
                    return None
                if raw == "true":
                    return True
                if raw == "false":
                    return False
                try:
                    return float(raw) if "." in raw else int(raw)
                except Exception:
                    return raw.strip('"')
    return None


def _candidate_identity_positions(text: str, expected_item_id: int) -> list[int]:
    patterns = (
        rf'"id"\s*:\s*"?{expected_item_id}"?',
        rf'\\"id\\"\s*:\s*"?{expected_item_id}"?',
        rf'"item_id"\s*:\s*"?{expected_item_id}"?',
        rf'\\"item_id\\"\s*:\s*"?{expected_item_id}"?',
    )
    positions: list[int] = []
    for pattern in patterns:
        positions.extend(match.start() for match in re.finditer(pattern, text))
    return sorted(set(positions))


def normalize_public_item_html(html: str, *, expected_item_id: int) -> VintedMetricSample:
    """Extract exact public item metrics from Vinted's server-rendered Next.js page.

    Fail-closed rules are deliberate: a metric is accepted only from a text region
    that also contains the requested item identity. Missing values remain UNKNOWN.
    """
    measured_at = _utcnow_iso()
    sources = [_next_flight_blob(html), html]
    chosen_text = ""
    chosen_pos: int | None = None
    for text in sources:
        if not text:
            continue
        positions = _candidate_identity_positions(text, expected_item_id)
        if not positions:
            continue
        # Prefer an identity occurrence near an explicit item object / metric field.
        best: tuple[int, int] | None = None
        for pos in positions:
            window = text[max(0, pos - 10000): min(len(text), pos + 50000)]
            score = sum(token in window for token in ('"view_count"', '\\"view_count\\"', '"favourite_count"', '\\"favourite_count\\"', '"upload_date"', '"created_at'))
            candidate = (score, pos)
            if best is None or candidate > best:
                best = candidate
        if best is not None:
            chosen_text = text
            chosen_pos = best[1]
            if best[0] > 0:
                break

    if chosen_pos is None:
        return VintedMetricSample(
            item_id=expected_item_id,
            source="detail_html",
            measured_at=measured_at,
            identity_ok=False,
            outcome="identity_not_found",
            notes=["Requested item ID was not found in the public page hydration payload."],
        )

    view_count = _int_or_none(_json_scalar_near(chosen_text, chosen_pos, ("view_count", "views_count", "views")))
    favourite_count = _int_or_none(_json_scalar_near(chosen_text, chosen_pos, ("favourite_count", "favorites_count", "favourites_count")))
    upload_raw = _json_scalar_near(chosen_text, chosen_pos, ("created_at", "created_at_ts", "upload_date", "uploaded_at"))
    sold = _bool_or_none(_json_scalar_near(chosen_text, chosen_pos, ("is_sold", "sold")))
    closed = _bool_or_none(_json_scalar_near(chosen_text, chosen_pos, ("is_closed", "closed")))
    reserved = _bool_or_none(_json_scalar_near(chosen_text, chosen_pos, ("is_reserved", "reserved")))
    hidden = _bool_or_none(_json_scalar_near(chosen_text, chosen_pos, ("is_hidden", "hidden")))
    visible = _bool_or_none(_json_scalar_near(chosen_text, chosen_pos, ("is_visible", "visible")))

    notes: list[str] = []
    if view_count is None:
        notes.append("view_count absent from identity-bound public page payload: exact views remain UNKNOWN")
    if favourite_count is None:
        notes.append("favourite_count absent from identity-bound public page payload")
    if upload_raw is None:
        notes.append("upload chronology absent from identity-bound public page payload")

    return VintedMetricSample(
        item_id=expected_item_id,
        source="detail_html",
        measured_at=measured_at,
        view_count=view_count,
        favourite_count=favourite_count,
        upload_raw=upload_raw,
        sold=sold,
        closed=closed,
        reserved=reserved,
        hidden=hidden,
        visible=visible,
        identity_ok=True,
        outcome="ok",
        notes=notes,
    )


def _bool_or_none(value: Any) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return None


def _percentile_ms(records: list[ProbeHTTPRecord], kind: str, p: float) -> int | None:
    values = sorted(record.elapsed_ms for record in records if record.kind == kind and record.status_code is not None)
    if not values:
        return None
    if len(values) == 1:
        return values[0]
    rank = min(len(values) - 1, max(0, round((len(values) - 1) * p)))
    return values[rank]


def _average_ms(records: list[ProbeHTTPRecord], kind: str) -> float | None:
    values = [record.elapsed_ms for record in records if record.kind == kind and record.status_code is not None]
    if not values:
        return None
    return round(statistics.fmean(values), 1)


async def run_probe(config: VintedProbeConfig, *, transport: httpx.AsyncBaseTransport | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    async with VintedProbeClient(config, transport=transport) as client:
        bootstrap_outcome = await client.bootstrap()

        categories: tuple[int | None, ...] = config.catalog_ids or (None,)
        category_sem = asyncio.Semaphore(config.page_concurrency)
        page_results: list[tuple[int | None, int, list[VintedItem], str, int | float | str | None]] = []
        catalog_depth: list[dict[str, Any]] = []

        async def scan_catalog(catalog_id: int | None) -> dict[str, Any]:
            """Pages stay sequential inside one category; categories can run in parallel.

            This is the production shape we want for two Vinted Scan Workers:
            split categories between workers, but never race page 1/2/3 of the same
            live newest-first feed. When overlap reduces unique depth, continue a
            bounded number of extra pages until the requested unique depth is restored.
            """
            async with category_sem:
                seen: set[int] = set()
                target_unique = config.pages * config.per_page
                max_pages = config.pages + config.recovery_pages
                fetched = 0
                outcome = "ok"
                exhausted = False
                response_times: list[Any] = []
                for page in range(1, max_pages + 1):
                    items, page_outcome, response_time = await client.fetch_catalog_page(catalog_id, page)
                    page_results.append((catalog_id, page, items, page_outcome, response_time))
                    fetched += 1
                    if response_time is not None:
                        response_times.append(response_time)
                    if page_outcome != "ok":
                        outcome = page_outcome
                        break
                    seen.update(item.item_id for item in items)
                    if len(items) < config.per_page:
                        exhausted = True
                        break
                    if page >= config.pages and len(seen) >= target_unique:
                        break
                depth_ok = len(seen) >= target_unique or exhausted
                return {
                    "catalog_id": catalog_id,
                    "requested_pages": config.pages,
                    "fetched_pages": fetched,
                    "recovery_pages_used": max(0, fetched - config.pages),
                    "target_unique": target_unique,
                    "unique_seen": len(seen),
                    "exhausted": exhausted,
                    "recovery_complete": depth_ok,
                    "outcome": outcome,
                    "response_times": response_times,
                }

        catalog_depth = list(await asyncio.gather(*(scan_catalog(catalog_id) for catalog_id in categories)))

        items_by_id: dict[int, VintedItem] = {}
        catalog_failures: list[dict[str, Any]] = []
        raw_item_count = 0
        page_item_ids: dict[str, list[int]] = {}
        snapshot_times: dict[str, Any] = {}
        for catalog_id, page, items, outcome, snapshot_time in page_results:
            raw_item_count += len(items)
            key = f"{catalog_id if catalog_id is not None else 'ALL'}:{page}"
            page_item_ids[key] = [item.item_id for item in items]
            if snapshot_time is not None:
                snapshot_times[str(catalog_id if catalog_id is not None else "ALL")] = snapshot_time
            if outcome != "ok":
                catalog_failures.append({"catalog_id": catalog_id, "page": page, "outcome": outcome})
            for item in items:
                items_by_id.setdefault(item.item_id, item)

        unique_items = list(items_by_id.values())
        detail_targets = unique_items[: config.detail_sample]
        detail_sem = asyncio.Semaphore(config.detail_concurrency)

        async def fetch_detail(item: VintedItem):
            async with detail_sem:
                return await client.fetch_item_detail(item)

        detail_samples = await asyncio.gather(*(fetch_detail(item) for item in detail_targets)) if detail_targets else []

        stability_test: dict[str, Any] = {"status": "not_run", "item_id": None, "sequence": []}
        if config.stability_reads >= 2:
            exact_pairs = [(item, sample) for item, sample in zip(detail_targets, detail_samples) if sample.identity_ok and sample.view_count is not None]
            if exact_pairs:
                # Pick the lowest observed view count to reduce the chance that normal marketplace traffic
                # is mistaken for self-view contamination during this short diagnostic.
                stable_item, stable_sample = min(exact_pairs, key=lambda pair: int(pair[1].view_count or 0))
                sequence = [int(stable_sample.view_count or 0)]
                outcomes = [stable_sample.outcome]
                for _ in range(config.stability_reads - 1):
                    await asyncio.sleep(config.stability_delay_seconds)
                    repeated = await client.fetch_item_detail(stable_item)
                    outcomes.append(repeated.outcome)
                    if repeated.identity_ok and repeated.view_count is not None:
                        sequence.append(int(repeated.view_count))
                    else:
                        sequence.append(None)
                valid = [value for value in sequence if isinstance(value, int)]
                if len(valid) != len(sequence):
                    status = "inconclusive"
                elif len(set(valid)) == 1:
                    status = "stable"
                else:
                    status = "changed_during_probe"
                stability_test = {
                    "status": status,
                    "item_id": stable_item.item_id,
                    "sequence": sequence,
                    "outcomes": outcomes,
                    "delay_seconds": config.stability_delay_seconds,
                    "note": "A changed sequence is conservative evidence of possible self-view contamination or concurrent organic traffic; it is not automatically called a self-view.",
                }

        exact_view_samples = [sample for sample in detail_samples if sample.identity_ok and sample.view_count is not None]
        exact_favourite_samples = [sample for sample in detail_samples if sample.identity_ok and sample.favourite_count is not None]
        wrong_identity = [sample for sample in detail_samples if sample.outcome == "wrong_identity"]
        catalog_zero_views = sum(1 for item in unique_items if item.catalog_view_count == 0)
        catalog_nonzero_views = sum(1 for item in unique_items if (item.catalog_view_count or 0) > 0)
        catalog_unknown_views = sum(1 for item in unique_items if item.catalog_view_count is None)
        catalog_zero_favs = sum(1 for item in unique_items if item.catalog_favourite_count == 0)
        promoted_known = [item for item in unique_items if item.promoted is not None]
        promoted_true = [item for item in unique_items if item.promoted is True]

        # Per-page overlap makes pagination failures explainable instead of reducing
        # the issue to one global duplicate count.
        page_overlaps: list[dict[str, Any]] = []
        keys = list(page_item_ids)
        for i, key_a in enumerate(keys):
            cat_a, page_a = key_a.rsplit(":", 1)
            set_a = set(page_item_ids[key_a])
            for key_b in keys[i + 1:]:
                cat_b, page_b = key_b.rsplit(":", 1)
                if cat_a != cat_b:
                    continue
                set_b = set(page_item_ids[key_b])
                overlap = set_a & set_b
                if overlap:
                    page_overlaps.append({
                        "catalog": cat_a,
                        "page_a": int(page_a),
                        "page_b": int(page_b),
                        "overlap": len(overlap),
                    })

        status_counts: dict[str, int] = {}
        for record in client.records:
            status_counts[record.outcome] = status_counts.get(record.outcome, 0) + 1

        elapsed_seconds = round(time.perf_counter() - started, 3)
        report: dict[str, Any] = {
            "schema": "dt-vinted-probe-v3",
            "generated_at": _utcnow_iso(),
            "config": {
                "base_url": config.base_url,
                "catalog_ids": list(config.catalog_ids),
                "pages": config.pages,
                "per_page": config.per_page,
                "page_concurrency": config.page_concurrency,
                "detail_sample": config.detail_sample,
                "detail_concurrency": config.detail_concurrency,
                "min_interval_seconds": config.min_interval_seconds,
                "access_token_configured": bool(config.access_token_web),
                "session_json_configured": bool(config.session_json),
                "stability_reads": config.stability_reads,
                "stability_delay_seconds": config.stability_delay_seconds,
                "recovery_pages": config.recovery_pages,
            },
            "bootstrap_outcome": bootstrap_outcome,
            "session": {
                "cookie_names": client.bootstrap_cookie_names,
                "has_access_token_web": "access_token_web" in client.bootstrap_cookie_names or bool(config.access_token_web),
                "has_refresh_token_web": "refresh_token_web" in client.bootstrap_cookie_names,
                "public_oauth": client.oauth_outcome,
            },
            "elapsed_seconds": elapsed_seconds,
            "catalog": {
                "requests": len(page_results),
                "failures": catalog_failures,
                "items_returned": raw_item_count,
                "unique_items": len(unique_items),
                "duplicates": max(0, raw_item_count - len(unique_items)),
                "pagination_times": snapshot_times,
                "depth_recovery": catalog_depth,
                "page_overlaps": page_overlaps,
                "catalog_view_count": {
                    "unknown": catalog_unknown_views,
                    "zero": catalog_zero_views,
                    "nonzero": catalog_nonzero_views,
                },
                "catalog_favourite_count": {
                    "unknown": sum(1 for item in unique_items if item.catalog_favourite_count is None),
                    "zero": catalog_zero_favs,
                    "nonzero": sum(1 for item in unique_items if (item.catalog_favourite_count or 0) > 0),
                },
                "promoted_field_coverage": len(promoted_known),
                "promoted_true": len(promoted_true),
            },
            "detail": {
                "source": "web_item_api_then_web_details_then_public_oauth_mobile",
                "requested": len(detail_targets),
                "identity_ok": sum(1 for sample in detail_samples if sample.identity_ok),
                "wrong_identity": len(wrong_identity),
                "exact_view_samples": len(exact_view_samples),
                "exact_favourite_samples": len(exact_favourite_samples),
                "view_coverage_ratio": round(len(exact_view_samples) / len(detail_samples), 4) if detail_samples else 0.0,
                "favourite_coverage_ratio": round(len(exact_favourite_samples) / len(detail_samples), 4) if detail_samples else 0.0,
                "outcomes": _count_values(sample.outcome for sample in detail_samples),
                "upload_time_samples": sum(1 for sample in detail_samples if sample.identity_ok and sample.upload_raw is not None),
            },
            "view_stability": stability_test,
            "latency_ms": {
                "catalog_avg": _average_ms(client.records, "catalog"),
                "catalog_p95": _percentile_ms(client.records, "catalog", 0.95),
                "detail_web_item_avg": _average_ms(client.records, "detail_web_item"),
                "detail_web_details_avg": _average_ms(client.records, "detail_web_details"),
                "detail_mobile_item_avg": _average_ms(client.records, "detail_mobile_item"),
                "oauth_public_avg": _average_ms(client.records, "oauth_public"),
            },
            "http_outcomes": status_counts,
            "quality_gates": {},
            "sample_items": [asdict(item) for item in unique_items[:10]],
            "sample_metrics": [asdict(sample) for sample in detail_samples[:10]],
        }

        report["quality_gates"] = evaluate_quality_gates(report)
        if config.report_path:
            path = Path(config.report_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        return report

def _count_values(values: Iterable[str]) -> dict[str, int]:
    result: dict[str, int] = {}
    for value in values:
        result[value] = result.get(value, 0) + 1
    return result


def evaluate_quality_gates(report: dict[str, Any]) -> dict[str, Any]:
    catalog = report.get("catalog") if isinstance(report.get("catalog"), dict) else {}
    detail = report.get("detail") if isinstance(report.get("detail"), dict) else {}
    failures = catalog.get("failures") if isinstance(catalog.get("failures"), list) else []
    unique_items = int(catalog.get("unique_items") or 0)
    detail_requested = int(detail.get("requested") or 0)
    identity_ok = int(detail.get("identity_ok") or 0)
    exact_views = int(detail.get("exact_view_samples") or 0)
    upload_samples = int(detail.get("upload_time_samples") or 0)
    stability = report.get("view_stability") if isinstance(report.get("view_stability"), dict) else {}
    stability_status = str(stability.get("status") or "not_run")
    duplicate_ratio = (float(catalog.get("duplicates") or 0) / float(catalog.get("items_returned") or 1)) if int(catalog.get("items_returned") or 0) > 0 else 0.0
    depth_recovery = catalog.get("depth_recovery") if isinstance(catalog.get("depth_recovery"), list) else []
    pagination_ok = bool(depth_recovery) and all(
        bool(entry.get("recovery_complete")) and str(entry.get("outcome") or "") == "ok"
        for entry in depth_recovery
        if isinstance(entry, dict)
    )
    return {
        "catalog_access": {
            "pass": unique_items > 0 and not failures,
            "reason": "catalog JSON returned unique items" if unique_items > 0 and not failures else "catalog access incomplete or empty",
        },
        "identity": {
            "pass": detail_requested == 0 or identity_ok == detail_requested,
            "reason": f"{identity_ok}/{detail_requested} detail samples matched requested item ID",
        },
        "pagination_integrity": {
            "pass": unique_items > 0 and pagination_ok and not failures,
            "reason": f"bounded unique-depth recovery={'PASS' if pagination_ok else 'FAIL'}; raw duplicate ratio={duplicate_ratio:.3f}",
        },
        "exact_views": {
            "pass": detail_requested > 0 and exact_views == detail_requested,
            "reason": f"{exact_views}/{detail_requested} detail samples exposed an exact view_count; UNKNOWN is never converted to zero",
        },
        "chronology": {
            "pass": detail_requested > 0 and upload_samples == detail_requested,
            "reason": f"{upload_samples}/{detail_requested} exact-identity detail samples exposed upload chronology",
        },
        "view_stability": {
            "pass": stability_status == "stable",
            "reason": f"short repeated-read stability={stability_status}; changes are treated conservatively as possible contamination or concurrent traffic",
        },
        "radar_ready": {
            "pass": unique_items > 0 and detail_requested > 0 and identity_ok == detail_requested and exact_views == detail_requested and upload_samples == detail_requested and stability_status == "stable" and pagination_ok and not failures,
            "reason": "Radar remains disabled until catalog, pagination, identity, chronology, exact-view and short stability gates all pass",
        },
    }


def format_probe_summary(report: dict[str, Any]) -> str:
    catalog = report.get("catalog", {})
    detail = report.get("detail", {})
    latency = report.get("latency_ms", {})
    gates = report.get("quality_gates", {})
    depth = catalog.get("depth_recovery") if isinstance(catalog.get("depth_recovery"), list) else []
    recovery_bits = []
    for entry in depth:
        if not isinstance(entry, dict):
            continue
        label = entry.get("catalog_id") if entry.get("catalog_id") is not None else "ALL"
        recovery_bits.append(
            f"{label}:{entry.get('unique_seen')}/{entry.get('target_unique')}u "
            f"pages={entry.get('fetched_pages')} rec={entry.get('recovery_pages_used')} "
            f"{'OK' if entry.get('recovery_complete') else 'SHORT'}"
        )

    def gate(name: str) -> str:
        data = gates.get(name, {}) if isinstance(gates, dict) else {}
        return "PASS" if data.get("pass") else "FAIL"

    return "\n".join([
        "DT Vinted Probe v0.4",
        f"bootstrap={report.get('bootstrap_outcome')} session={report.get('session')} elapsed={report.get('elapsed_seconds')}s",
        f"catalog requests={catalog.get('requests')} unique={catalog.get('unique_items')} duplicates={catalog.get('duplicates')} failures={len(catalog.get('failures') or [])}",
        f"catalog recovery={' | '.join(recovery_bits) if recovery_bits else 'n/a'}",
        f"catalog latency avg={latency.get('catalog_avg')}ms p95={latency.get('catalog_p95')}ms",
        f"detail requested={detail.get('requested')} identity_ok={detail.get('identity_ok')} exact_views={detail.get('exact_view_samples')} exact_favourites={detail.get('exact_favourite_samples')} chronology={detail.get('upload_time_samples')}",
        f"detail web/item avg={latency.get('detail_web_item_avg')}ms web/details avg={latency.get('detail_web_details_avg')}ms mobile/item avg={latency.get('detail_mobile_item_avg')}ms oauth={latency.get('oauth_public_avg')}ms",
        f"view_stability={report.get('view_stability')}",
        f"gates: catalog={gate('catalog_access')} pagination={gate('pagination_integrity')} identity={gate('identity')} chronology={gate('chronology')} exact_views={gate('exact_views')} stability={gate('view_stability')} radar_ready={gate('radar_ready')}",
        f"http_outcomes={report.get('http_outcomes')}",
    ])


__all__ = [
    "VintedProbeConfig",
    "VintedProbeClient",
    "VintedItem",
    "VintedMetricSample",
    "run_probe",
    "evaluate_quality_gates",
    "format_probe_summary",
    "normalize_catalog_item",
    "normalize_detail_metrics",
    "normalize_public_item_html",
]
