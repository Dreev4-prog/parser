from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import re
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://www.kleinanzeigen.de"
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)
HARD_MAX_PAGES_PER_CATEGORY = 100
MAX_PAGES_PER_CATEGORY = min(
    HARD_MAX_PAGES_PER_CATEGORY,
    max(1, int(os.getenv("MAX_PAGES_PER_CATEGORY", str(HARD_MAX_PAGES_PER_CATEGORY)))),
)
PAGE_DELAY_SECONDS = max(0.0, float(os.getenv("PAGE_DELAY_SECONDS", "1.0")))
CATEGORY_HTTP_RETRIES = max(1, min(4, int(os.getenv("CATEGORY_HTTP_RETRIES", "3"))))
CATEGORY_403_BACKOFF_SECONDS = max(3.0, float(os.getenv("CATEGORY_403_BACKOFF_SECONDS", "10")))
CATEGORY_RETRY_JITTER_SECONDS = max(0.0, float(os.getenv("CATEGORY_RETRY_JITTER_SECONDS", "2.0")))
STOP_AFTER_EMPTY_TODAY_PAGES = max(1, int(os.getenv("STOP_AFTER_EMPTY_TODAY_PAGES", "2")))
AVAILABILITY_TIMEOUT = max(5.0, float(os.getenv("AVAILABILITY_TIMEOUT", "20")))
VIEW_COUNT_GLOBAL_CONCURRENCY = max(1, min(16, int(os.getenv("VIEW_COUNT_GLOBAL_CONCURRENCY", "6"))))
DIRECT_VIEW_CONCURRENCY = max(1, min(16, int(os.getenv("DIRECT_VIEW_CONCURRENCY", "8"))))
_GLOBAL_VIEW_SEMAPHORE = None


def _global_view_semaphore() -> asyncio.Semaphore:
    global _GLOBAL_VIEW_SEMAPHORE
    if _GLOBAL_VIEW_SEMAPHORE is None:
        _GLOBAL_VIEW_SEMAPHORE = asyncio.Semaphore(VIEW_COUNT_GLOBAL_CONCURRENCY)
    return _GLOBAL_VIEW_SEMAPHORE

log = logging.getLogger("kleinanzeigen-parser")


class TemporaryAccessError(RuntimeError):
    """The public site temporarily refused category requests after gentle retries."""

    def __init__(self, status_code: int, url: str):
        self.status_code = int(status_code)
        self.url = url
        super().__init__(f"Kleinanzeigen временно ограничил запросы (HTTP {status_code})")


@dataclass
class ViewCountResult:
    views: int | None
    raw_text: str | None
    source: str
    final_url: str | None = None
    page_title: str | None = None
    error: str | None = None




@dataclass
class ViewNetworkProbe:
    views: int | None
    source: str
    final_url: str | None
    page_title: str | None
    candidates: list[dict]
    element_html: str | None = None
    error: str | None = None
    diagnostic: dict | None = None


@dataclass
class ParsedListing:
    external_id: str
    title: str
    price_text: str | None
    price_eur: int | None
    url: str
    posted_text: str | None = None


PRICE_NUMBER_RE = re.compile(r"(?<!\d)(\d{1,3}(?:\.\d{3})*|\d+)\s*€(?:\s*(?:VB|Festpreis))?", re.IGNORECASE)
PRICE_WORD_RE = re.compile(r"\b(?:Zu verschenken|VB|Festpreis)\b", re.IGNORECASE)
UNAVAILABLE_PHRASES = (
    "die gewünschte anzeige ist nicht mehr verfügbar",
    "die gewuenschte anzeige ist nicht mehr verfuegbar",
    "anzeige nicht mehr verfügbar",
    "anzeige nicht mehr verfuegbar",
)


def parse_price(text: str | None) -> int | None:
    if not text:
        return None
    match = PRICE_NUMBER_RE.search(text)
    if not match:
        return None
    return int(match.group(1).replace(".", ""))


def extract_external_id(url: str) -> str:
    match = re.search(r"/(\d{6,})-\d+-\d+(?:[/?#]|$)", url)
    if match:
        return match.group(1)
    nums = re.findall(r"\d{6,}", url)
    return nums[0] if nums else url


def _allowed_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "kleinanzeigen.de" or host.endswith(".kleinanzeigen.de"))


def page_url(base_url: str, page: int) -> str:
    if page <= 1:
        return base_url
    parts = urlsplit(base_url)
    path = re.sub(r"/(c\d+)$", rf"/seite%3A{page}/\1", parts.path)
    if path == parts.path:
        raise ValueError(f"Unsupported category URL: {base_url}")
    return urlunsplit((parts.scheme, parts.netloc, path, parts.query, parts.fragment))


def _extract_posted_text(node) -> str | None:
    text = node.get_text(" ", strip=True)
    patterns = [
        r"\bHeute(?:,?\s*\d{1,2}:\d{2})?\b",
        r"\bGestern(?:,?\s*\d{1,2}:\d{2})?\b",
        r"\b\d{1,2}\.\d{1,2}\.\d{4}\b",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
    return None


def is_today_text(text: str | None) -> bool:
    return bool(text and re.search(r"\bHeute\b", text, flags=re.IGNORECASE))


def _clean_price_candidate(text: str) -> str | None:
    text = " ".join(text.split()).strip()
    if not text or len(text) > 90:
        return None
    m = PRICE_NUMBER_RE.search(text)
    if m:
        value = m.group(0).strip()
        # Preserve VB/Festpreis if it immediately follows the amount.
        tail = text[m.end():].strip()
        if re.match(r"^(VB|Festpreis)\b", tail, flags=re.IGNORECASE):
            value += " " + re.match(r"^(VB|Festpreis)\b", tail, flags=re.IGNORECASE).group(1)
        return value
    if re.fullmatch(r"\s*Zu verschenken\s*", text, flags=re.IGNORECASE):
        return "Zu verschenken"
    if re.fullmatch(r"\s*VB\s*", text, flags=re.IGNORECASE):
        return "VB"
    return None


def _extract_price_text(node) -> str | None:
    # Prefer the actual price container. Kleinanzeigen has changed class names
    # several times, so keep multiple selectors and a text fallback.
    selectors = [
        ".aditem-main--middle--price-shipping--price",
        "[class*='price-shipping--price']",
        "[class*='price-shipping'] [class*='price']",
        "[data-testid*='price']",
        "[class*='price']",
    ]
    for selector in selectors:
        for element in node.select(selector):
            candidate = _clean_price_candidate(element.get_text(" ", strip=True))
            if candidate:
                return candidate

    # Fallback: inspect short descendant text fragments. Prefer fragments that
    # contain an amount and are not shipping-only labels such as "+ Versand ab 1,49 €".
    candidates: list[tuple[int, str]] = []
    for element in node.find_all(["p", "div", "span", "strong"]):
        text = " ".join(element.get_text(" ", strip=True).split())
        candidate = _clean_price_candidate(text)
        if not candidate:
            continue
        lower = text.lower()
        score = len(text)
        if "versand" in lower:
            score += 1000
        if "price" in " ".join(element.get("class", [])).lower():
            score -= 100
        candidates.append((score, candidate))
    if candidates:
        candidates.sort(key=lambda x: x[0])
        return candidates[0][1]

    # Last fallback over the card text. Search amount before shipping amounts.
    card_text = " ".join(node.get_text(" ", strip=True).split())
    matches = list(PRICE_NUMBER_RE.finditer(card_text))
    for m in matches:
        prefix = card_text[max(0, m.start() - 20):m.start()].lower()
        if "versand" not in prefix:
            return m.group(0).strip()
    if re.search(r"\bZu verschenken\b", card_text, flags=re.IGNORECASE):
        return "Zu verschenken"
    return None


PROMOTED_CLASS_TOKENS = (
    "topad",
    "top-ad",
    "badge-topad",
    "is-topad",
    "highlight",
    "is-highlight",
    "sponsored",
    "sponsor",
    "promoted",
    "promotion",
    "werbeanzeige",
    "werbung",
)
PROMOTED_LABEL_RE = re.compile(
    r"\b(?:Top[- ]?Anzeige|Werbeanzeige|Gesponsert|Sponsored|Promoted)\b",
    re.IGNORECASE,
)
FILTER_PROMOTED_LISTINGS = os.getenv("FILTER_PROMOTED_LISTINGS", "1").strip().lower() not in {
    "0", "false", "no", "off"
}


def _is_promoted_listing_node(node) -> bool:
    """Detect paid/promoted cards in Kleinanzeigen search results.

    Detection deliberately relies on explicit promotion markers from the search card
    (classes, data/aria labels and visible labels), not on view-count heuristics. This
    avoids throwing away genuinely viral fresh listings.
    """
    if not FILTER_PROMOTED_LISTINGS:
        return False

    def attrs_blob(element) -> str:
        parts: list[str] = []
        for key in ("class", "id", "data-testid", "data-test", "data-adtype",
                    "data-type", "aria-label", "title"):
            value = element.get(key) if hasattr(element, "get") else None
            if isinstance(value, (list, tuple)):
                parts.extend(str(x) for x in value)
            elif value:
                parts.append(str(value))
        return " ".join(parts).lower()

    # The marker can live on the list item itself, on a parent wrapper, or on a
    # badge inside the card. Include a few ancestors because our broad candidate
    # selector can yield both the outer <li> and the inner <article>.
    elements = [node]
    try:
        parent = node.parent
        for _ in range(4):
            if parent is None or getattr(parent, "name", None) in {"body", "html"}:
                break
            elements.append(parent)
            parent = parent.parent
        elements.extend(node.find_all(True))
    except Exception:
        pass

    for element in elements:
        blob = attrs_blob(element)
        if blob and any(token in blob for token in PROMOTED_CLASS_TOKENS):
            return True

        # Promotion badges are often short labels. Inspect only short text nodes so
        # normal descriptions mentioning words like "Highlight" are not excluded.
        try:
            text = " ".join(element.get_text(" ", strip=True).split())
        except Exception:
            text = ""
        if text and len(text) <= 40 and PROMOTED_LABEL_RE.search(text):
            return True

    return False


def parse_category_html(html_text: str) -> list[ParsedListing]:
    soup = BeautifulSoup(html_text, "html.parser")
    items: list[ParsedListing] = []
    seen_urls: set[str] = set()

    candidates = soup.select("article, li.ad-listitem, .ad-listitem")
    for node in candidates:
        if _is_promoted_listing_node(node):
            continue

        link = node.select_one("a[href*='/s-anzeige/']")
        if not link or not link.get("href"):
            continue

        full_url = urljoin(BASE_URL, link["href"])
        if full_url in seen_urls or not _allowed_url(full_url):
            continue
        seen_urls.add(full_url)

        title_node = node.select_one("h2, .ellipsis, [class*='title']")
        title = (title_node or link).get_text(" ", strip=True)
        if not title:
            continue

        price_text = _extract_price_text(node)
        posted_text = _extract_posted_text(node)

        items.append(
            ParsedListing(
                external_id=extract_external_id(full_url),
                title=title,
                price_text=price_text,
                price_eur=parse_price(price_text),
                url=full_url,
                posted_text=posted_text,
            )
        )

    return items


PASSIVE_VIEW_ENDPOINT_RE = re.compile(r"/s-vac-inc-get\.json(?:\?|$)", re.IGNORECASE)
VIEW_KEY_RE = re.compile(r"(?:^|[_-])(view(?:s|count)?|counter|cntr|aufruf(?:e)?|impression(?:s)?|visit(?:s)?)(?:$|[_-])", re.IGNORECASE)


def _coerce_nonnegative_int(value) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if 0 <= value <= 999_999_999 else None
    if isinstance(value, float) and value.is_integer():
        iv = int(value)
        return iv if 0 <= iv <= 999_999_999 else None
    if isinstance(value, str):
        raw = value.strip()
        if re.fullmatch(r"\d{1,9}", raw):
            return int(raw)
        if re.fullmatch(r"\d{1,3}(?:[.\s]\d{3})+", raw):
            return int(re.sub(r"\D", "", raw))
    return None


def _extract_passive_view_payload(text: str, *, ad_id: str | None = None) -> tuple[int | None, str | None]:
    """Extract a likely public view count from a response Kleinanzeigen itself requested.

    This function NEVER performs the request. It only parses the response body captured
    while a normal public ad page is loading. Keyed values such as views/viewCount/
    counter/Aufrufe are preferred. For the known s-vac-inc-get endpoint a single scalar
    integer is accepted as a conservative fallback.
    """
    if not text:
        return None, None
    body = text.strip()
    if not body:
        return None, None

    try:
        data = json.loads(body)
    except Exception:
        data = None

    keyed: list[tuple[int, str, int]] = []
    scalars: list[tuple[str, int]] = []

    def walk(value, path: str = "$"):
        if isinstance(value, dict):
            for k, v in value.items():
                key = str(k)
                next_path = f"{path}.{key}"
                iv = _coerce_nonnegative_int(v)
                if iv is not None:
                    scalars.append((next_path, iv))
                    low = key.lower().replace("-", "_")
                    score = 0
                    if low in {"views", "view", "viewcount", "view_count", "aufrufe", "counter", "cntr"}:
                        score = 10
                    elif VIEW_KEY_RE.search(low):
                        score = 7
                    elif low in {"count", "num", "value"}:
                        score = 3
                    if score:
                        keyed.append((score, next_path, iv))
                walk(v, next_path)
        elif isinstance(value, list):
            for i, v in enumerate(value[:100]):
                walk(v, f"{path}[{i}]")
        else:
            iv = _coerce_nonnegative_int(value)
            if iv is not None:
                scalars.append((path, iv))

    if data is not None:
        walk(data)
        if keyed:
            keyed.sort(key=lambda x: (-x[0], len(x[1]), x[1]))
            score, path, value = keyed[0]
            return value, f"json:{path}"

        # Conservative endpoint-specific fallback: one numeric scalar other than the ad ID.
        ad_num = int(ad_id) if ad_id and ad_id.isdigit() else None
        unique = []
        seen = set()
        for path, value in scalars:
            if value == ad_num or value in seen:
                continue
            seen.add(value)
            unique.append((path, value))
        if len(unique) == 1:
            path, value = unique[0]
            return value, f"json-single:{path}"

    # Plain-text number is also a safe shape for this specific captured endpoint.
    iv = _coerce_nonnegative_int(body)
    if iv is not None and (not ad_id or str(iv) != ad_id):
        return iv, "text:integer"

    # Narrow textual keyed fallback; avoid arbitrary numbers such as ad IDs/statuses.
    m = re.search(
        r'(?i)(?:"|\\b)(views?|view[_-]?count|counter|cntr|aufrufe?|impressions?|visits?)(?:"|\\b)\\s*[:=]\\s*"?(\\d{1,9})"?',
        body,
    )
    if m:
        return int(m.group(2)), f"text-key:{m.group(1)}"
    return None, None


class KleinanzeigenParser:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=30.0,
            follow_redirects=True,
        )
        self._playwright = None
        self._browser = None
        self._browser_context = None
        self._browser_lock = asyncio.Lock()
        self._direct_mode_lock = asyncio.Lock()
        self._direct_view_mode: str = "unknown"  # unknown|http|context|browser
        self._context_session_seeded = False

    async def close(self) -> None:
        try:
            if self._browser_context is not None:
                await self._browser_context.close()
        except Exception:
            pass
        try:
            if self._browser is not None:
                await self._browser.close()
        except Exception:
            pass
        try:
            if self._playwright is not None:
                await self._playwright.stop()
        except Exception:
            pass
        self._browser_context = None
        self._browser = None
        self._playwright = None
        await self.client.aclose()

    async def fetch_html(self, url: str) -> str:
        if not _allowed_url(url):
            raise ValueError("Only public kleinanzeigen.de HTTPS URLs are allowed")

        last_error: Exception | None = None
        for attempt in range(CATEGORY_HTTP_RETRIES):
            try:
                response = await self.client.get(url)
                status = response.status_code

                # Do not try to defeat site protection. A temporary 403/429 gets a
                # small respectful cooldown and a bounded retry; persistent refusal
                # is surfaced to the caller so the scan can stop cleanly.
                if status in {403, 429}:
                    if attempt >= CATEGORY_HTTP_RETRIES - 1:
                        raise TemporaryAccessError(status, url)
                    delay = min(45.0, CATEGORY_403_BACKOFF_SECONDS * (attempt + 1))
                    delay += random.uniform(0.0, CATEGORY_RETRY_JITTER_SECONDS)
                    log.warning(
                        "Category request temporarily refused status=%s attempt=%s/%s; cooling down %.1fs: %s",
                        status, attempt + 1, CATEGORY_HTTP_RETRIES, delay, url,
                    )
                    await asyncio.sleep(delay)
                    continue

                response.raise_for_status()
                return response.text
            except TemporaryAccessError:
                raise
            except httpx.TimeoutException as exc:
                last_error = exc
                if attempt >= CATEGORY_HTTP_RETRIES - 1:
                    raise
                await asyncio.sleep(1.5 * (attempt + 1) + random.uniform(0.0, 0.8))
            except httpx.HTTPStatusError as exc:
                last_error = exc
                status = exc.response.status_code
                if attempt >= CATEGORY_HTTP_RETRIES - 1 or status not in {500, 502, 503, 504}:
                    raise
                await asyncio.sleep(1.5 * (attempt + 1) + random.uniform(0.0, 0.8))
        raise RuntimeError(str(last_error))

    async def parse_category_page(self, url: str) -> list[ParsedListing]:
        return parse_category_html(await self.fetch_html(url))


    @staticmethod
    def _view_count_from_html(html_text: str) -> tuple[int | None, str | None]:
        """Fast path: some Kleinanzeigen responses already contain the counter in HTML."""
        soup = BeautifulSoup(html_text, "html.parser")
        node = soup.select_one("#viewad-cntr-num")
        if node is not None:
            raw = node.get_text(" ", strip=True)
            match = re.search(r"\d[\d.\s]*", raw)
            if match:
                return int(re.sub(r"\D", "", match.group(0))), raw
        # Narrow source fallback; do not scan arbitrary page numbers.
        m = re.search(
            r'id=["\']viewad-cntr-num["\'][^>]*>\s*([0-9][0-9.\s]*)\s*<',
            html_text, flags=re.IGNORECASE,
        )
        if m:
            raw = m.group(1).strip()
            return int(re.sub(r"\D", "", raw)), raw
        return None, None

    async def _ensure_view_browser(self):
        if self._browser_context is not None:
            return self._browser_context
        async with self._browser_lock:
            if self._browser_context is not None:
                return self._browser_context
            from playwright.async_api import async_playwright
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            self._browser_context = await self._browser.new_context(
                user_agent=USER_AGENT,
                locale="de-DE",
                viewport={"width": 1365, "height": 900},
                extra_http_headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.7"},
            )
            return self._browser_context

    async def _install_lightweight_route(self, page) -> None:
        async def route_handler(route):
            if route.request.resource_type in {"image", "font", "media", "stylesheet"}:
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", route_handler)

    def _attach_passive_view_capture(self, page, ad_id: str | None):
        """Return (future, tasks) for the view response naturally requested by the page."""
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        tasks: list[asyncio.Task] = []

        async def capture(response):
            try:
                if future.done():
                    return
                if not PASSIVE_VIEW_ENDPOINT_RE.search(response.url):
                    return
                text = await response.text()
                value, shape = _extract_passive_view_payload(text[:350_000], ad_id=ad_id)
                if value is not None and not future.done():
                    future.set_result((value, text[:500], response.url, shape))
            except Exception:
                pass

        def on_response(response):
            if PASSIVE_VIEW_ENDPOINT_RE.search(response.url):
                try:
                    tasks.append(asyncio.create_task(capture(response)))
                except Exception:
                    pass

        page.on("response", on_response)
        return future, tasks

    @staticmethod
    def _view_value_from_text(text: str | None) -> tuple[int | None, str | None]:
        if not text:
            return None, None
        raw = " ".join(text.split()).strip()
        if not raw:
            return None, None
        m = re.search(r"(?<!\d)(\d{1,3}(?:[.\s]\d{3})+|\d{1,9})(?!\d)", raw)
        if not m:
            return None, raw
        digits = re.sub(r"\D", "", m.group(1))
        if not digits:
            return None, raw
        value = int(digits)
        if value < 0 or value > 999_999_999:
            return None, raw
        return value, raw

    @classmethod
    def _view_value_from_extra_text(cls, text: str | None) -> tuple[int | None, str | None]:
        """Fallback for A/B markup where the eye counter lost its old id.

        The extra-info block normally contains only the publication date/time and
        the public view count. Remove date/time tokens first, then use the last
        remaining standalone integer as the counter.
        """
        if not text:
            return None, None
        raw = " ".join(text.split()).strip()
        cleaned = raw
        cleaned = re.sub(r"\b\d{1,2}\.\d{1,2}\.\d{4}\b", " ", cleaned)
        cleaned = re.sub(r"\b(?:Heute|Gestern)\b", " ", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\b\d{1,2}:\d{2}\b", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        nums = re.findall(r"(?<!\d)(\d{1,9})(?!\d)", cleaned)
        if not nums:
            return None, raw
        value = int(nums[-1])
        if value > 999_999_999:
            return None, raw
        return value, raw

    async def _extract_view_count_from_page(self, page) -> tuple[int | None, str | None, str]:
        """Extract the public counter from several current/legacy DOM shapes.

        Kleinanzeigen has A/B-tested the ad header markup. The historical
        #viewad-cntr-num selector is still preferred, but we also inspect the
        surrounding extra-info block, view-related data/test ids, and embedded
        application JSON. This does not use authentication or bypass challenges.
        """
        selectors = [
            "#viewad-cntr-num",
            "[data-testid='view-count']",
            "[data-testid*='view'][data-testid*='count']",
            "[id*='view'][id*='cntr']",
            "[id*='view'][id*='count']",
            "[class*='view'][class*='count']",
        ]
        for selector in selectors:
            try:
                loc = page.locator(selector)
                count = await loc.count()
                for i in range(min(count, 4)):
                    raw = (await loc.nth(i).inner_text()).strip()
                    value, raw_norm = self._view_value_from_text(raw)
                    if value is not None:
                        return value, raw_norm, f"dom:{selector}"
            except Exception:
                pass

        # Very useful fallback: the public date + eye counter live in this block.
        for selector in ("#viewad-extra-info", "[id*='viewad-extra-info']", "[data-testid*='extra-info']"):
            try:
                loc = page.locator(selector)
                if await loc.count():
                    raw = (await loc.first.inner_text()).strip()
                    value, raw_norm = self._view_value_from_extra_text(raw)
                    if value is not None:
                        return value, raw_norm, f"dom:{selector}:text"
            except Exception:
                pass

        # Search small DOM nodes whose own metadata explicitly refers to views.
        try:
            candidates = await page.evaluate(
                """
                () => {
                  const out = [];
                  const nodes = document.querySelectorAll('span,div,p,small,strong');
                  for (const el of nodes) {
                    const attrs = [el.id, el.className, el.getAttribute('data-testid'),
                      el.getAttribute('aria-label'), el.getAttribute('title')]
                      .filter(Boolean).join(' ').toLowerCase();
                    if (!/(view|cntr|aufruf|impression|eye)/.test(attrs)) continue;
                    const text = (el.innerText || el.textContent || '').trim();
                    if (!text || text.length > 100) continue;
                    out.push({attrs, text});
                    if (out.length >= 40) break;
                  }
                  return out;
                }
                """
            )
            for item in candidates or []:
                value, raw_norm = self._view_value_from_text(item.get("text"))
                if value is not None:
                    return value, raw_norm, "dom:view-metadata"
        except Exception:
            pass

        # Some builds hydrate the value from JSON before rendering it.
        try:
            scripts = await page.locator("script").all_text_contents()
            patterns = [
                r'"views"\s*:\s*"?(\d{1,9})"?',
                r'"viewCount"\s*:\s*"?(\d{1,9})"?',
                r'"view_count"\s*:\s*"?(\d{1,9})"?',
                r'"impressions"\s*:\s*"?(\d{1,9})"?',
            ]
            for text in scripts:
                if not text or len(text) < 10:
                    continue
                for pattern in patterns:
                    m = re.search(pattern, text, flags=re.IGNORECASE)
                    if m:
                        return int(m.group(1)), m.group(0), "script:hydration-json"
        except Exception:
            pass

        return None, None, "browser:not-found"

    async def _page_diagnostic(self, page) -> dict:
        """Small non-sensitive diagnostics for explaining a missing public counter."""
        diag = {"final_url": None, "title": None, "extra_info": None, "classification": "normal"}
        try:
            diag["final_url"] = page.url
            diag["title"] = await page.title()
        except Exception:
            pass
        try:
            extra = page.locator("#viewad-extra-info")
            if await extra.count():
                diag["extra_info"] = " ".join((await extra.first.inner_text()).split())[:240]
        except Exception:
            pass
        try:
            body = (await page.locator("body").inner_text()).lower()[:12000]
            challenge_words = (
                "captcha", "access denied", "sicherheitsüberprüfung", "sicherheitsueberpruefung",
                "ungewöhnliche aktivität", "ungewoehnliche aktivitaet", "robot", "bot protection",
            )
            if any(w in body for w in challenge_words):
                diag["classification"] = "challenge"
            elif "cookie" in body and "zustimmen" in body and len(body) < 3500:
                diag["classification"] = "consent-only"
            elif "/s-anzeige/" not in (page.url or ""):
                diag["classification"] = "redirected"
        except Exception:
            pass
        return diag

    @staticmethod
    def _direct_view_url(ad_id: str) -> str:
        return f"{BASE_URL}/s-vac-inc-get.json?adId={ad_id}"

    @staticmethod
    def _direct_view_headers(referer: str) -> dict[str, str]:
        return {
            "Accept": "application/json,text/plain,*/*",
            "Referer": referer,
            "X-Requested-With": "XMLHttpRequest",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        }

    async def _direct_view_http(self, url: str) -> ViewCountResult:
        ad_id = extract_external_id(url)
        endpoint = self._direct_view_url(ad_id)
        try:
            response = await self.client.get(
                endpoint,
                headers=self._direct_view_headers(url),
                timeout=12.0,
            )
            if response.status_code != 200:
                return ViewCountResult(
                    None, None, f"direct-http:status-{response.status_code}",
                    str(response.url), None,
                    error=f"HTTP {response.status_code}",
                )
            text = response.text
            value, shape = _extract_passive_view_payload(text[:350_000], ad_id=ad_id)
            if value is None:
                return ViewCountResult(None, text[:500], "direct-http:unparsed", str(response.url), None)
            return ViewCountResult(
                int(value), text[:500], f"direct-http:s-vac-inc-get:{shape or 'payload'}",
                str(response.url), None,
            )
        except Exception as exc:
            return ViewCountResult(None, None, "direct-http:error", error=str(exc)[:300])

    async def _seed_context_request_session(self, context) -> None:
        if self._context_session_seeded:
            return
        try:
            response = await context.request.get(
                BASE_URL + "/",
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
                },
                timeout=15_000,
                fail_on_status_code=False,
            )
            if 200 <= response.status < 500:
                self._context_session_seeded = True
        except Exception:
            pass

    async def _direct_view_context_request(self, url: str) -> ViewCountResult:
        ad_id = extract_external_id(url)
        endpoint = self._direct_view_url(ad_id)
        try:
            context = await self._ensure_view_browser()
            response = await context.request.get(
                endpoint,
                headers=self._direct_view_headers(url),
                timeout=12_000,
                fail_on_status_code=False,
            )
            if response.status in {403, 429} and not self._context_session_seeded:
                await self._seed_context_request_session(context)
                response = await context.request.get(
                    endpoint,
                    headers=self._direct_view_headers(url),
                    timeout=12_000,
                    fail_on_status_code=False,
                )
            if response.status != 200:
                return ViewCountResult(
                    None, None, f"direct-context:status-{response.status}",
                    endpoint, None, error=f"HTTP {response.status}",
                )
            text = await response.text()
            value, shape = _extract_passive_view_payload(text[:350_000], ad_id=ad_id)
            if value is None:
                return ViewCountResult(None, text[:500], "direct-context:unparsed", endpoint, None)
            return ViewCountResult(
                int(value), text[:500], f"direct-context:s-vac-inc-get:{shape or 'payload'}",
                endpoint, None,
            )
        except Exception as exc:
            return ViewCountResult(None, None, "direct-context:error", error=str(exc)[:300])

    async def probe_direct_view_mode(self, url: str, *, force: bool = False) -> tuple[str, ViewCountResult]:
        """Find the cheapest working public counter path once per parser instance.

        Order: plain HTTP -> Playwright APIRequestContext -> normal browser page.
        The direct endpoint may increment the public counter, just like opening the ad page.
        """
        if not force and self._direct_view_mode != "unknown":
            return self._direct_view_mode, ViewCountResult(None, None, f"mode:{self._direct_view_mode}")
        async with self._direct_mode_lock:
            if not force and self._direct_view_mode != "unknown":
                return self._direct_view_mode, ViewCountResult(None, None, f"mode:{self._direct_view_mode}")
            direct = await self._direct_view_http(url)
            if direct.views is not None:
                self._direct_view_mode = "http"
                return "http", direct
            context_direct = await self._direct_view_context_request(url)
            if context_direct.views is not None:
                self._direct_view_mode = "context"
                return "context", context_direct
            self._direct_view_mode = "browser"
            return "browser", context_direct

    async def fetch_public_view_count_direct(self, url: str, *, mode: str | None = None) -> ViewCountResult:
        """Fast counter fetch that does not render an ad page when a direct path works."""
        if not _allowed_url(url) or "/s-anzeige/" not in url:
            return ViewCountResult(None, None, "invalid-url", error="Нужна публичная ссылка на объявление Kleinanzeigen")
        chosen = mode or self._direct_view_mode
        if chosen == "unknown":
            chosen, probe = await self.probe_direct_view_mode(url)
            if probe.views is not None:
                return probe
        if chosen == "http":
            return await self._direct_view_http(url)
        if chosen == "context":
            return await self._direct_view_context_request(url)
        return ViewCountResult(None, None, "direct:browser-required")

    async def fetch_public_view_count(self, url: str, *, browser_fallback: bool = True, http_fast_path: bool = True) -> ViewCountResult:
        """Read the public view counter without authentication.

        First try the normal HTML response (cheap). If the counter is injected only
        after JavaScript, reuse one Playwright browser/context for all calls made by
        this parser instance instead of starting a Chromium process per listing.
        """
        if not _allowed_url(url) or "/s-anzeige/" not in url:
            return ViewCountResult(None, None, "invalid-url", error="Нужна публичная ссылка на объявление Kleinanzeigen")

        if http_fast_path:
            try:
                html_text = await self.fetch_html(url)
                value, raw = self._view_count_from_html(html_text)
                if value is not None:
                    return ViewCountResult(value, raw, "http:#viewad-cntr-num", url, None)
            except Exception as exc:
                log.debug("View-count HTTP fast path failed for %s: %s", url, exc)

        if not browser_fallback:
            return ViewCountResult(None, None, "http:not-found", url, None)

        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
        except Exception as exc:
            return ViewCountResult(None, None, "playwright-unavailable", error=str(exc))

        page = None
        try:
            context = await self._ensure_view_browser()
            page = await context.new_page()
            await self._install_lightweight_route(page)
            ad_id = extract_external_id(url)
            passive_future, passive_tasks = self._attach_passive_view_capture(page, ad_id)

            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            final_url = page.url
            page_title = await page.title()

            # Fast path in v2.6.9: wait briefly for the public counter endpoint that
            # Kleinanzeigen itself requests while rendering this page. We do NOT call
            # that endpoint ourselves, so there is no extra counter request.
            try:
                value, raw_payload, endpoint_url, shape = await asyncio.wait_for(
                    asyncio.shield(passive_future), timeout=3.0
                )
                return ViewCountResult(
                    int(value), raw_payload,
                    f"network:passive:s-vac-inc-get:{shape or 'payload'}",
                    final_url, page_title,
                )
            except (asyncio.TimeoutError, PlaywrightTimeoutError):
                pass

            try:
                await page.wait_for_selector("#viewad-title", state="attached", timeout=4_000)
            except PlaywrightTimeoutError:
                pass
            # DOM is the reliable fallback if the network response shape changes.
            try:
                await page.wait_for_selector("#viewad-cntr-num", state="attached", timeout=3_000)
            except PlaywrightTimeoutError:
                pass
            await page.wait_for_timeout(350)

            value, raw, source = await self._extract_view_count_from_page(page)
            if value is not None:
                return ViewCountResult(value, raw, source, final_url, page_title)

            diag = await self._page_diagnostic(page)
            error = f"page={diag.get('classification')}"
            if diag.get("extra_info"):
                error += f"; extra={diag['extra_info'][:160]}"
            return ViewCountResult(None, None, source, final_url, page_title, error)
        except Exception as exc:
            log.warning("View-count browser fetch failed for %s: %s", url, exc)
            return ViewCountResult(None, None, "browser:error", error=str(exc)[:500])
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    async def inspect_view_network(self, url: str) -> ViewNetworkProbe:
        """Inspect the public ad page and report likely network sources for the view counter.

        This is deliberately diagnostic: it records only public response URLs and small
        text snippets from XHR/fetch responses. Cookies, request headers and auth data
        are never returned.
        """
        if not _allowed_url(url) or "/s-anzeige/" not in url:
            return ViewNetworkProbe(None, "invalid-url", None, None, [], error="Нужна публичная ссылка Kleinanzeigen")

        page = None
        capture_tasks: list[asyncio.Task] = []
        captured: list[dict] = []
        try:
            context = await self._ensure_view_browser()
            page = await context.new_page()
            await self._install_lightweight_route(page)

            async def capture_response(response):
                try:
                    rtype = response.request.resource_type
                    if rtype not in {"xhr", "fetch"}:
                        return
                    ctype = (response.headers.get("content-type") or "").lower()
                    if not any(x in ctype for x in ("json", "text", "javascript", "xml")):
                        return
                    if len(captured) >= 80:
                        return
                    text = await response.text()
                    if len(text) > 350_000:
                        text = text[:350_000]
                    parsed_views = None
                    parsed_shape = None
                    if PASSIVE_VIEW_ENDPOINT_RE.search(response.url):
                        parsed_views, parsed_shape = _extract_passive_view_payload(
                            text, ad_id=extract_external_id(url)
                        )
                    captured.append({
                        "url": response.url,
                        "status": response.status,
                        "type": rtype,
                        "content_type": ctype[:120],
                        "body": text,
                        "passive_views": parsed_views,
                        "passive_shape": parsed_shape,
                    })
                except Exception:
                    pass

            def on_response(response):
                try:
                    capture_tasks.append(asyncio.create_task(capture_response(response)))
                except Exception:
                    pass

            page.on("response", on_response)
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            final_url = page.url
            page_title = await page.title()
            try:
                await page.wait_for_selector("#viewad-title", state="attached", timeout=8_000)
            except Exception:
                pass
            try:
                await page.wait_for_selector("#viewad-cntr-num", state="attached", timeout=6_000)
            except Exception:
                pass
            await page.wait_for_timeout(1200)

            views, raw_view_text, view_source = await self._extract_view_count_from_page(page)
            element_html = None
            for candidate_selector in ("#viewad-cntr-num", "#viewad-extra-info"):
                try:
                    locator = page.locator(candidate_selector)
                    if await locator.count():
                        element_html = await locator.first.evaluate(
                            "el => el.parentElement ? el.parentElement.outerHTML : el.outerHTML"
                        )
                        break
                except Exception:
                    pass

            if capture_tasks:
                try:
                    await asyncio.wait_for(asyncio.gather(*capture_tasks, return_exceptions=True), timeout=4.0)
                except Exception:
                    pass

            candidates: list[dict] = []
            keywords = ("view", "views", "viewcount", "counter", "cntr", "aufruf", "impression")
            view_token = str(views) if views is not None else None
            for item in captured:
                u = item["url"]
                body = item["body"]
                low_u = u.lower()
                low_b = body.lower()
                score = 0
                reasons = []
                if PASSIVE_VIEW_ENDPOINT_RE.search(u):
                    score += 10
                    reasons.append("passive-counter-endpoint")
                    if item.get("passive_views") is not None:
                        score += 8
                        reasons.append(f"parsed-views:{item['passive_views']}")
                if any(k in low_u for k in keywords):
                    score += 4
                    reasons.append("keyword-in-url")
                body_keyword = next((k for k in keywords if k in low_b), None)
                if body_keyword:
                    score += 3
                    reasons.append(f"keyword:{body_keyword}")
                if view_token and re.search(rf"(?<!\d){re.escape(view_token)}(?!\d)", body):
                    score += 2
                    reasons.append("contains-current-count")
                if score <= 0:
                    continue
                # Small, safe context only; no headers/cookies are exposed.
                snippet = ""
                pos = -1
                for k in keywords:
                    pos = low_b.find(k)
                    if pos >= 0:
                        break
                if pos < 0 and view_token:
                    pos = body.find(view_token)
                if pos >= 0:
                    start = max(0, pos - 100)
                    end = min(len(body), pos + 220)
                    snippet = re.sub(r"\s+", " ", body[start:end]).strip()
                candidates.append({
                    "score": score,
                    "url": u,
                    "status": item["status"],
                    "type": item["type"],
                    "content_type": item["content_type"],
                    "reasons": reasons,
                    "snippet": snippet[:320],
                    "passive_views": item.get("passive_views"),
                    "passive_shape": item.get("passive_shape"),
                })

            candidates.sort(key=lambda x: (-x["score"], x["url"]))
            # Deduplicate by URL and keep only a concise top set.
            dedup = []
            seen = set()
            for c in candidates:
                if c["url"] in seen:
                    continue
                seen.add(c["url"])
                dedup.append(c)
                if len(dedup) >= 8:
                    break

            diag = await self._page_diagnostic(page)
            return ViewNetworkProbe(
                views,
                view_source if views is not None else "browser:not-found",
                final_url,
                page_title,
                dedup,
                element_html,
                None if views is not None else f"page={diag.get('classification')}",
                diag,
            )
        except Exception as exc:
            log.warning("View network probe failed for %s: %s", url, exc)
            return ViewNetworkProbe(None, "browser:error", None, None, [], error=str(exc)[:500])
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    async def fetch_public_view_counts(self, urls: list[str], *, concurrency: int = 6, progress_cb=None) -> dict[str, ViewCountResult]:
        """Batch public view counters with a direct-first fast path.

        v2.7.2 probes the public s-vac-inc-get endpoint once. If plain HTTP or a
        Playwright APIRequestContext works, counters are fetched without rendering ad
        pages. Only failed counters fall back to the lightweight browser-page method.
        """
        if not urls:
            return {}

        # Preserve order while dropping duplicates.
        urls = list(dict.fromkeys(urls))
        results: dict[str, ViewCountResult] = {}
        total = len(urls)

        mode, probe = await self.probe_direct_view_mode(urls[0])
        if probe.views is not None:
            results[urls[0]] = probe

        direct_sem = asyncio.Semaphore(DIRECT_VIEW_CONCURRENCY)
        done_count = len(results)
        done_lock = asyncio.Lock()

        async def report_progress():
            if progress_cb is None:
                return
            try:
                maybe = progress_cb(done_count, total)
                if asyncio.iscoroutine(maybe):
                    await maybe
            except Exception:
                pass

        async def direct_one(url: str):
            nonlocal done_count
            if url in results:
                return
            async with direct_sem:
                vr = await self.fetch_public_view_count_direct(url, mode=mode)
                results[url] = vr
            async with done_lock:
                done_count += 1
            await report_progress()

        if mode in {"http", "context"}:
            await asyncio.gather(*(direct_one(url) for url in urls if url not in results))
        else:
            # Browser mode: leave all URLs for the fallback pass below.
            results = {}
            done_count = 0

        failed_urls = [url for url in urls if results.get(url) is None or results[url].views is None]
        if not failed_urls:
            await report_progress()
            return results

        # Browser fallback is intentionally gentler than direct requests. This is
        # only used for the minority of URLs that the fast endpoint cannot read.
        page_sem = asyncio.Semaphore(max(1, min(6, concurrency)))
        global_sem = _global_view_semaphore()

        async def browser_one(url: str):
            nonlocal done_count
            async with page_sem:
                async with global_sem:
                    vr = await self.fetch_public_view_count(url, http_fast_path=False)
                    results[url] = vr
            # Direct attempts already incremented progress; in pure browser mode they did not.
            if mode == "browser":
                async with done_lock:
                    done_count += 1
                await report_progress()

        chunk_size = max(12, concurrency * 5)
        for i in range(0, len(failed_urls), chunk_size):
            chunk = failed_urls[i:i + chunk_size]
            await asyncio.gather(*(browser_one(url) for url in chunk))
        if mode != "browser":
            # Progress was already counted during direct attempts; final fallback only
            # changes quality, not the number of processed URLs.
            await report_progress()
        return results

    async def check_listing_active(self, url: str) -> bool | None:
        """Return True=live, False=unavailable, None=could not determine safely."""
        if not _allowed_url(url):
            return None
        try:
            response = await self.client.get(url, timeout=AVAILABILITY_TIMEOUT)
        except httpx.HTTPError:
            return None
        if response.status_code in {404, 410}:
            return False
        if response.status_code in {403, 429} or response.status_code >= 500:
            return None
        text = BeautifulSoup(response.text, "html.parser").get_text(" ", strip=True).lower()
        if any(phrase in text for phrase in UNAVAILABLE_PHRASES):
            return False
        # A live detail page normally still contains its ad ID or a detail heading.
        external_id = extract_external_id(url)
        if external_id and external_id in response.text:
            return True
        if "/s-anzeige/" in str(response.url):
            return True
        return None
