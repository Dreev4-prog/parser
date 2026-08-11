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

    async def close(self) -> None:
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


    async def fetch_public_view_count(self, url: str) -> ViewCountResult:
        """Read the public view counter rendered by the Kleinanzeigen detail page.

        Kleinanzeigen currently renders the counter in ``#viewad-cntr-num`` after
        JavaScript runs. This method intentionally uses a real headless browser and
        does not log in, inject cookies, or bypass access controls.
        """
        if not _allowed_url(url) or "/s-anzeige/" not in url:
            return ViewCountResult(None, None, "invalid-url", error="Нужна публичная ссылка на объявление Kleinanzeigen")

        try:
            from playwright.async_api import TimeoutError as PlaywrightTimeoutError
            from playwright.async_api import async_playwright
        except Exception as exc:
            return ViewCountResult(None, None, "playwright-unavailable", error=str(exc))

        browser = None
        context = None
        try:
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(
                    headless=True,
                    args=["--no-sandbox", "--disable-dev-shm-usage"],
                )
                context = await browser.new_context(
                    user_agent=USER_AGENT,
                    locale="de-DE",
                    viewport={"width": 1365, "height": 900},
                    extra_http_headers={"Accept-Language": "de-DE,de;q=0.9,en;q=0.7"},
                )
                page = await context.new_page()

                # Images/fonts/media are not needed for the counter and make Railway
                # detail-page tests slower. Scripts/XHR remain enabled.
                async def route_handler(route):
                    if route.request.resource_type in {"image", "font", "media"}:
                        await route.abort()
                    else:
                        await route.continue_()

                await page.route("**/*", route_handler)
                await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
                final_url = page.url
                page_title = await page.title()

                selector = "#viewad-cntr-num"
                try:
                    await page.wait_for_selector(selector, state="attached", timeout=12_000)
                except PlaywrightTimeoutError:
                    pass

                locator = page.locator(selector)
                if await locator.count():
                    raw = (await locator.first.inner_text()).strip()
                    match = re.search(r"\d[\d.\s]*", raw)
                    if match:
                        value = int(re.sub(r"\D", "", match.group(0)))
                        return ViewCountResult(value, raw, "dom:#viewad-cntr-num", final_url, page_title)

                # Fallback: the counter sits inside the extra-info block. Keep this
                # deliberately narrow so dates/prices cannot be mistaken for views.
                extra = page.locator("#viewad-extra-info")
                if await extra.count():
                    extra_text = (await extra.first.inner_text()).strip()
                    # Prefer the number from an element whose id/class mentions the counter.
                    raw_html = await extra.first.inner_html()
                    m = re.search(
                        r"(?:viewad-cntr-num|cntr-num)[^>]*>\s*([0-9][0-9.\s]*)\s*<",
                        raw_html,
                        flags=re.IGNORECASE,
                    )
                    if m:
                        raw = m.group(1).strip()
                        value = int(re.sub(r"\D", "", raw))
                        return ViewCountResult(value, raw, "dom:extra-info", final_url, page_title)

                return ViewCountResult(None, None, "browser:not-found", final_url, page_title)
        except Exception as exc:
            log.exception("View-count browser test failed for %s", url)
            return ViewCountResult(None, None, "browser:error", error=str(exc)[:500])
        finally:
            try:
                if context is not None:
                    await context.close()
            except Exception:
                pass
            try:
                if browser is not None:
                    await browser.close()
            except Exception:
                pass

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
