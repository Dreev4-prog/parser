from __future__ import annotations

import asyncio
import logging
import os
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
PAGE_DELAY_SECONDS = max(0.0, float(os.getenv("PAGE_DELAY_SECONDS", "0.7")))
STOP_AFTER_EMPTY_TODAY_PAGES = max(1, int(os.getenv("STOP_AFTER_EMPTY_TODAY_PAGES", "2")))
AVAILABILITY_TIMEOUT = max(5.0, float(os.getenv("AVAILABILITY_TIMEOUT", "20")))

log = logging.getLogger("kleinanzeigen-parser")


@dataclass
class ViewCountResult:
    views: int | None
    raw_text: str | None
    source: str
    final_url: str | None = None
    page_title: str | None = None
    error: str | None = None


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


def parse_category_html(html_text: str) -> list[ParsedListing]:
    soup = BeautifulSoup(html_text, "html.parser")
    items: list[ParsedListing] = []
    seen_urls: set[str] = set()

    candidates = soup.select("article, li.ad-listitem, .ad-listitem")
    for node in candidates:
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
        for attempt in range(3):
            try:
                response = await self.client.get(url)
                response.raise_for_status()
                return response.text
            except (httpx.TimeoutException, httpx.HTTPStatusError) as exc:
                last_error = exc
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                if attempt >= 2 or (status is not None and status not in {429, 500, 502, 503, 504}):
                    raise
                await asyncio.sleep(1.5 * (attempt + 1))
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

    async def fetch_public_view_count(self, url: str, *, browser_fallback: bool = True) -> ViewCountResult:
        """Read the public view counter without authentication.

        First try the normal HTML response (cheap). If the counter is injected only
        after JavaScript, reuse one Playwright browser/context for all calls made by
        this parser instance instead of starting a Chromium process per listing.
        """
        if not _allowed_url(url) or "/s-anzeige/" not in url:
            return ViewCountResult(None, None, "invalid-url", error="Нужна публичная ссылка на объявление Kleinanzeigen")

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

            async def route_handler(route):
                if route.request.resource_type in {"image", "font", "media"}:
                    await route.abort()
                else:
                    await route.continue_()

            await page.route("**/*", route_handler)
            await page.goto(url, wait_until="domcontentloaded", timeout=45_000)
            final_url = page.url
            page_title = await page.title()

            selector = "#viewad-cntr-num"
            try:
                await page.wait_for_selector(selector, state="attached", timeout=8_000)
            except PlaywrightTimeoutError:
                pass

            locator = page.locator(selector)
            if await locator.count():
                raw = (await locator.first.inner_text()).strip()
                match = re.search(r"\d[\d.\s]*", raw)
                if match:
                    value = int(re.sub(r"\D", "", match.group(0)))
                    return ViewCountResult(value, raw, "dom:#viewad-cntr-num", final_url, page_title)

            extra = page.locator("#viewad-extra-info")
            if await extra.count():
                raw_html = await extra.first.inner_html()
                m = re.search(
                    r"(?:viewad-cntr-num|cntr-num)[^>]*>\s*([0-9][0-9.\s]*)\s*<",
                    raw_html, flags=re.IGNORECASE,
                )
                if m:
                    raw = m.group(1).strip()
                    value = int(re.sub(r"\D", "", raw))
                    return ViewCountResult(value, raw, "dom:extra-info", final_url, page_title)

            return ViewCountResult(None, None, "browser:not-found", final_url, page_title)
        except Exception as exc:
            log.warning("View-count browser fetch failed for %s: %s", url, exc)
            return ViewCountResult(None, None, "browser:error", error=str(exc)[:500])
        finally:
            if page is not None:
                try:
                    await page.close()
                except Exception:
                    pass

    async def fetch_public_view_counts(self, urls: list[str], *, concurrency: int = 6, progress_cb=None) -> dict[str, ViewCountResult]:
        """Batch view-count fetch with one reusable browser and bounded concurrency."""
        sem = asyncio.Semaphore(max(1, min(10, concurrency)))
        results: dict[str, ViewCountResult] = {}

        async def one(url: str):
            async with sem:
                results[url] = await self.fetch_public_view_count(url)

        # Work in chunks so thousands of listings do not create thousands of live tasks.
        chunk_size = max(20, concurrency * 8)
        total = len(urls)
        for i in range(0, total, chunk_size):
            chunk = urls[i:i + chunk_size]
            await asyncio.gather(*(one(url) for url in chunk))
            if progress_cb is not None:
                try:
                    maybe = progress_cb(min(total, i + len(chunk)), total)
                    if asyncio.iscoroutine(maybe):
                        await maybe
                except Exception:
                    pass
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
