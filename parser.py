import asyncio
import json
import os
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://www.kleinanzeigen.de"
DELAY = float(os.getenv("REQUEST_DELAY_SECONDS", "2.0"))
VIEW_MODE = os.getenv("VIEW_MODE", "auto").strip().lower()  # auto | http | browser
BROWSER_WAIT_MS = int(os.getenv("BROWSER_WAIT_MS", "2500"))
BROWSER_TIMEOUT_MS = int(os.getenv("BROWSER_TIMEOUT_MS", "20000"))
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
)


@dataclass
class ParsedListing:
    external_id: str
    title: str
    price_text: str | None
    price_eur: int | None
    url: str


@dataclass
class ViewResult:
    views: int | None
    source: str


def parse_price(text: str | None) -> int | None:
    if not text:
        return None
    cleaned = text.replace(".", "").replace("€", "").strip()
    match = re.search(r"(\d+)", cleaned)
    return int(match.group(1)) if match else None


def extract_external_id(url: str) -> str:
    # Typical detail URL: /slug/3460964524-279-6273
    nums = re.findall(r"\d{6,}", url)
    return nums[-1] if nums else url


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, float) and value.is_integer() and value >= 0:
        return int(value)
    if isinstance(value, str):
        cleaned = value.strip().replace(".", "").replace(",", "")
        if cleaned.isdigit():
            return int(cleaned)
    return None


def extract_views_from_text(text: str) -> int | None:
    # Only explicit labels. Avoid interpreting unrelated numbers as views.
    patterns = [
        r"([\d\.]+)\s*(?:Aufrufe|Aufrufen)",
        r"(?:Aufrufe|Aufrufen)\s*[:：]?\s*([\d\.]+)",
        r"([\d\.]+)\s*Besucher(?:innen)?(?:\s|$)",
        r"Besucher(?:innen)?\s*[:：]?\s*([\d\.]+)",
        r"([\d\.]+)\s*Views(?:\s|$)",
        r"Views\s*[:：]?\s*([\d\.]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return int(match.group(1).replace(".", ""))
    return None


def extract_views_from_raw_html(html: str) -> int | None:
    # Some sites serialize dynamic state in script tags even when the number
    # is not rendered in visible text. These patterns intentionally require a
    # view-related key/attribute right next to the number.
    patterns = [
        r'["\'](?:viewCount|viewsCount|numberOfViews|numViews|adViews|listingViews|visitorCount)["\']\s*:\s*["\']?([\d\.]+)',
        r'data-(?:view-count|views-count|views|visitor-count)=["\']([\d\.]+)["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html, flags=re.IGNORECASE)
        if match:
            return int(match.group(1).replace(".", ""))
    return None


_VIEW_KEYS = {
    "views",
    "viewcount",
    "viewscount",
    "numberofviews",
    "numviews",
    "adviews",
    "listingviews",
    "visitorcount",
}


def find_views_in_json(data: Any, path: str = "$") -> tuple[int | None, str | None]:
    """Find a numeric value under a tightly-scoped view-count key."""
    if isinstance(data, dict):
        for key, value in data.items():
            normalized = re.sub(r"[^a-z0-9]", "", str(key).lower())
            if normalized in _VIEW_KEYS:
                number = _to_int(value)
                if number is not None:
                    return number, f"{path}.{key}"
        for key, value in data.items():
            found, found_path = find_views_in_json(value, f"{path}.{key}")
            if found is not None:
                return found, found_path
    elif isinstance(data, list):
        for i, value in enumerate(data[:500]):
            found, found_path = find_views_in_json(value, f"{path}[{i}]")
            if found is not None:
                return found, found_path
    return None, None


def _allowed_url(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    return parsed.scheme == "https" and (host == "kleinanzeigen.de" or host.endswith(".kleinanzeigen.de"))


class KleinanzeigenParser:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            timeout=20.0,
            follow_redirects=True,
        )
        self._playwright = None
        self._browser = None
        self._browser_context = None

    async def close(self) -> None:
        await self.client.aclose()
        if self._browser_context is not None:
            await self._browser_context.close()
        if self._browser is not None:
            await self._browser.close()
        if self._playwright is not None:
            await self._playwright.stop()

    async def fetch_html(self, url: str) -> str:
        if not _allowed_url(url):
            raise ValueError("Only public kleinanzeigen.de HTTPS URLs are allowed")
        response = await self.client.get(url)
        response.raise_for_status()
        return response.text

    async def parse_category_page(self, url: str) -> list[ParsedListing]:
        html = await self.fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")
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

            price_node = node.select_one("[class*='price'], .aditem-main--middle--price-shipping--price")
            price_text = price_node.get_text(" ", strip=True) if price_node else None

            items.append(
                ParsedListing(
                    external_id=extract_external_id(full_url),
                    title=title,
                    price_text=price_text,
                    price_eur=parse_price(price_text),
                    url=full_url,
                )
            )

        return items

    async def _views_via_http(self, listing_url: str) -> ViewResult:
        html = await self.fetch_html(listing_url)

        raw = extract_views_from_raw_html(html)
        if raw is not None:
            return ViewResult(raw, "http:embedded-data")

        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        visible = extract_views_from_text(text)
        if visible is not None:
            return ViewResult(visible, "http:visible-text")

        # Also inspect JSON script tags individually; this avoids treating
        # unrelated JavaScript code as structured data.
        for script in soup.find_all("script"):
            script_type = (script.get("type") or "").lower()
            if "json" not in script_type:
                continue
            raw_json = script.string or script.get_text("", strip=True)
            if not raw_json:
                continue
            try:
                payload = json.loads(raw_json)
            except Exception:
                continue
            views, path = find_views_in_json(payload)
            if views is not None:
                return ViewResult(views, f"http:json:{path}")

        return ViewResult(None, "http:not-exposed")

    async def _ensure_browser(self) -> None:
        if self._browser_context is not None:
            return
        try:
            from playwright.async_api import async_playwright
        except ImportError as exc:
            raise RuntimeError(
                "Playwright is not installed. Install requirements.txt or set VIEW_MODE=http."
            ) from exc

        self._playwright = await async_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        self._browser_context = await self._browser.new_context(
            user_agent=USER_AGENT,
            locale="de-DE",
            viewport={"width": 1280, "height": 900},
        )

    async def _views_via_browser(self, listing_url: str) -> ViewResult:
        await self._ensure_browser()
        assert self._browser_context is not None

        page = await self._browser_context.new_page()
        network_candidates: list[tuple[int, str]] = []
        response_tasks: set[asyncio.Task] = set()

        async def inspect_response(response) -> None:
            try:
                content_type = (response.headers.get("content-type") or "").lower()
                if "json" not in content_type:
                    return
                payload = await response.json()
                views, path = find_views_in_json(payload)
                if views is not None:
                    network_candidates.append((views, f"browser:network:{path}"))
            except Exception:
                return

        def on_response(response) -> None:
            task = asyncio.create_task(inspect_response(response))
            response_tasks.add(task)
            task.add_done_callback(response_tasks.discard)

        page.on("response", on_response)

        try:
            await page.goto(listing_url, wait_until="domcontentloaded", timeout=BROWSER_TIMEOUT_MS)
            await page.wait_for_timeout(BROWSER_WAIT_MS)

            if response_tasks:
                await asyncio.gather(*list(response_tasks), return_exceptions=True)

            # 1) Rendered visible text.
            try:
                text = await page.locator("body").inner_text(timeout=3000)
                visible = extract_views_from_text(text)
                if visible is not None:
                    return ViewResult(visible, "browser:visible-text")
            except Exception:
                pass

            # 2) Final DOM, including dynamically inserted script/state data.
            try:
                html = await page.content()
                embedded = extract_views_from_raw_html(html)
                if embedded is not None:
                    return ViewResult(embedded, "browser:embedded-data")
            except Exception:
                pass

            # 3) JSON responses requested by the page.
            if network_candidates:
                # Prefer the largest candidate. Tiny values such as 0/1 often
                # belong to UI state, while a real accumulated view count is
                # usually the more informative metric.
                views, source = max(network_candidates, key=lambda item: item[0])
                return ViewResult(views, source)

            return ViewResult(None, "browser:not-exposed")
        finally:
            await page.close()

    async def parse_views(self, listing_url: str) -> ViewResult:
        await asyncio.sleep(DELAY)

        mode = VIEW_MODE if VIEW_MODE in {"auto", "http", "browser"} else "auto"

        if mode in {"auto", "http"}:
            try:
                http_result = await self._views_via_http(listing_url)
                if http_result.views is not None or mode == "http":
                    return http_result
            except Exception as exc:
                if mode == "http":
                    return ViewResult(None, f"http:error:{type(exc).__name__}")

        if mode in {"auto", "browser"}:
            try:
                return await self._views_via_browser(listing_url)
            except Exception as exc:
                return ViewResult(None, f"browser:error:{type(exc).__name__}")

        return ViewResult(None, "not-exposed")
