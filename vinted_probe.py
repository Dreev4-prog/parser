from __future__ import annotations

import asyncio
import json
import os
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
    report_path: str = ""
    stability_reads: int = 3
    stability_delay_seconds: float = 1.2

    @classmethod
    def from_env(cls) -> "VintedProbeConfig":
        raw_catalogs = os.getenv("VINTED_PROBE_CATALOG_IDS", "").strip()
        catalog_ids: list[int] = []
        for chunk in raw_catalogs.split(","):
            value = chunk.strip()
            if value.isdigit():
                catalog_ids.append(int(value))
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
            report_path=os.getenv("VINTED_PROBE_REPORT_PATH", "").strip(),
            stability_reads=max(0, min(5, int(os.getenv("VINTED_PROBE_STABILITY_READS", "3")))),
            stability_delay_seconds=max(0.2, min(10.0, float(os.getenv("VINTED_PROBE_STABILITY_DELAY_SECONDS", "1.2")))),
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
        token = config.access_token_web.strip()
        if token:
            host = urlparse(config.base_url).hostname or "www.vinted.de"
            self.client.cookies.set("access_token_web", token, domain=host, path="/")

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

    async def _request(self, kind: str, url: str, *, params: dict[str, Any] | None = None, expect_json: bool = True) -> tuple[httpx.Response | None, Any | None, str]:
        await self._gate.wait()
        started = time.perf_counter()
        try:
            response = await self.client.get(url, params=params)
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

    async def bootstrap(self) -> str:
        # A normal anonymous page request may establish cookies. If Vinted requires
        # a browser-only challenge, fail closed and report it rather than bypassing it.
        response, _text, outcome = await self._request("bootstrap", f"{self.config.base_url}/", expect_json=False)
        if response is None:
            return outcome
        return outcome

    async def fetch_catalog_page(self, catalog_id: int | None, page: int) -> tuple[list[VintedItem], str]:
        params: dict[str, Any] = {
            "page": page,
            "per_page": self.config.per_page,
            "order": "newest_first",
        }
        if catalog_id is not None:
            params["catalog_ids"] = catalog_id
        _response, payload, outcome = await self._request(
            "catalog",
            f"{self.config.base_url}/api/v2/catalog/items",
            params=params,
        )
        if outcome != "ok" or not isinstance(payload, dict):
            return [], outcome
        raw_items = payload.get("items")
        if not isinstance(raw_items, list):
            return [], "invalid_catalog_shape"
        items: list[VintedItem] = []
        for raw in raw_items:
            item = normalize_catalog_item(raw, self.config.base_url)
            if item is not None:
                items.append(item)
        return items, "ok"

    async def fetch_item_detail(self, item: VintedItem) -> VintedMetricSample:
        _response, payload, outcome = await self._request(
            "detail_api",
            f"{self.config.base_url}/api/v2/items/{item.item_id}",
        )
        if outcome != "ok" or not isinstance(payload, dict):
            return VintedMetricSample(
                item_id=item.item_id,
                source="detail_api",
                measured_at=_utcnow_iso(),
                identity_ok=False,
                outcome=outcome,
                notes=["No exact metric accepted from this response."],
            )
        return normalize_detail_metrics(payload, expected_item_id=item.item_id)


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
        catalog_id=_int_or_none(raw.get("catalog_id")),
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

        catalog_targets: list[tuple[int | None, int]] = []
        categories: tuple[int | None, ...] = config.catalog_ids or (None,)
        for catalog_id in categories:
            for page in range(1, config.pages + 1):
                catalog_targets.append((catalog_id, page))

        page_sem = asyncio.Semaphore(config.page_concurrency)

        async def fetch_target(catalog_id: int | None, page: int):
            async with page_sem:
                items, outcome = await client.fetch_catalog_page(catalog_id, page)
                return catalog_id, page, items, outcome

        page_results = await asyncio.gather(*(fetch_target(catalog_id, page) for catalog_id, page in catalog_targets))

        items_by_id: dict[int, VintedItem] = {}
        catalog_failures: list[dict[str, Any]] = []
        raw_item_count = 0
        for catalog_id, page, items, outcome in page_results:
            raw_item_count += len(items)
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

        status_counts: dict[str, int] = {}
        for record in client.records:
            status_counts[record.outcome] = status_counts.get(record.outcome, 0) + 1

        elapsed_seconds = round(time.perf_counter() - started, 3)
        report: dict[str, Any] = {
            "schema": "dt-vinted-probe-v1",
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
                "stability_reads": config.stability_reads,
                "stability_delay_seconds": config.stability_delay_seconds,
            },
            "bootstrap_outcome": bootstrap_outcome,
            "elapsed_seconds": elapsed_seconds,
            "catalog": {
                "requests": len(catalog_targets),
                "failures": catalog_failures,
                "items_returned": raw_item_count,
                "unique_items": len(unique_items),
                "duplicates": max(0, raw_item_count - len(unique_items)),
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
                "detail_avg": _average_ms(client.records, "detail_api"),
                "detail_p95": _percentile_ms(client.records, "detail_api", 0.95),
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
            "pass": unique_items > 0 and duplicate_ratio <= 0.10,
            "reason": f"deduplicated catalog duplicate ratio={duplicate_ratio:.3f}",
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
            "pass": unique_items > 0 and detail_requested > 0 and identity_ok == detail_requested and exact_views == detail_requested and upload_samples == detail_requested and stability_status == "stable" and duplicate_ratio <= 0.10,
            "reason": "Radar remains disabled until catalog, pagination, identity, chronology, exact-view and short stability gates all pass",
        },
    }


def format_probe_summary(report: dict[str, Any]) -> str:
    catalog = report.get("catalog", {})
    detail = report.get("detail", {})
    latency = report.get("latency_ms", {})
    gates = report.get("quality_gates", {})
    def gate(name: str) -> str:
        data = gates.get(name, {}) if isinstance(gates, dict) else {}
        return "PASS" if data.get("pass") else "FAIL"
    return "\n".join([
        "DT Vinted Probe v0.1",
        f"bootstrap={report.get('bootstrap_outcome')} elapsed={report.get('elapsed_seconds')}s",
        f"catalog requests={catalog.get('requests')} unique={catalog.get('unique_items')} duplicates={catalog.get('duplicates')} failures={len(catalog.get('failures') or [])}",
        f"catalog latency avg={latency.get('catalog_avg')}ms p95={latency.get('catalog_p95')}ms",
        f"detail requested={detail.get('requested')} identity_ok={detail.get('identity_ok')} exact_views={detail.get('exact_view_samples')} exact_favourites={detail.get('exact_favourite_samples')}",
        f"detail latency avg={latency.get('detail_avg')}ms p95={latency.get('detail_p95')}ms",
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
]
