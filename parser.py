import asyncio
import os
import re
from dataclasses import dataclass
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

BASE_URL = "https://www.kleinanzeigen.de"
DELAY = float(os.getenv("REQUEST_DELAY_SECONDS", "2.0"))
USER_AGENT = os.getenv(
    "USER_AGENT",
    "Mozilla/5.0 (compatible; KleinanzeigenCategoryMonitor/1.0; personal-use)",
)


@dataclass
class ParsedListing:
    external_id: str
    title: str
    price_text: str | None
    price_eur: int | None
    url: str
    views: int | None = None


def parse_price(text: str | None) -> int | None:
    if not text:
        return None
    cleaned = text.replace(".", "").replace("€", "").strip()
    match = re.search(r"(\d+)", cleaned)
    return int(match.group(1)) if match else None


def extract_external_id(url: str) -> str:
    # Kleinanzeigen links normally end with the numeric ad id.
    match = re.search(r"/(\d+)(?:-|/|$)", url[::-1])
    if match:
        return match.group(1)[::-1]
    nums = re.findall(r"\d{6,}", url)
    return nums[-1] if nums else url


class KleinanzeigenParser:
    def __init__(self) -> None:
        self.client = httpx.AsyncClient(
            headers={
                "User-Agent": USER_AGENT,
                "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
            },
            timeout=20.0,
            follow_redirects=True,
        )

    async def close(self) -> None:
        await self.client.aclose()

    async def fetch_html(self, url: str) -> str:
        response = await self.client.get(url)
        response.raise_for_status()
        return response.text

    async def parse_category_page(self, url: str) -> list[ParsedListing]:
        html = await self.fetch_html(url)
        soup = BeautifulSoup(html, "html.parser")
        items: list[ParsedListing] = []
        seen_urls: set[str] = set()

        # Keep selectors deliberately tolerant: Kleinanzeigen changes markup over time.
        candidates = soup.select("article, li.ad-listitem, .ad-listitem")
        for node in candidates:
            link = node.select_one("a[href*='/s-anzeige/']")
            if not link or not link.get("href"):
                continue

            full_url = urljoin(BASE_URL, link["href"])
            if full_url in seen_urls:
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

    async def parse_views(self, listing_url: str) -> int | None:
        await asyncio.sleep(DELAY)
        html = await self.fetch_html(listing_url)
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)

        # German variants seen on marketplace pages: "Aufrufe", "Besucher".
        patterns = [
            r"([\d\.]+)\s*Aufrufe",
            r"Aufrufe\s*[:]?\s*([\d\.]+)",
            r"([\d\.]+)\s*Besucher",
        ]
        for pattern in patterns:
            match = re.search(pattern, text, flags=re.IGNORECASE)
            if match:
                return int(match.group(1).replace(".", ""))
        return None
