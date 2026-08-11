from __future__ import annotations

import re
import statistics
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from models import Listing

BERLIN = ZoneInfo("Europe/Berlin")

# Conservative defaults: remove obvious wanted/service/repost noise, but do NOT
# remove words such as "defekt" because broken devices can still be real goods.
DEFAULT_NOISE_WORDS = {
    "suche", "gesucht", "ankauf", "kaufe", "kaufen", "reparatur",
    "repariere", "service", "dienstleistung", "vermietung", "verleih",
    "tausche", "tausch", "wanted",
}

TITLE_STOPWORDS = {
    "verkaufe", "verkauft", "biete", "angebot", "original", "top", "sehr",
    "guter", "gute", "gutes", "guten", "zustand", "neu", "neuwertig",
    "gebraucht", "inkl", "inklusive", "mit", "ohne", "und", "oder", "der",
    "die", "das", "ein", "eine", "einer", "einen", "für", "fuer", "von",
    "zu", "zum", "zur", "aus", "ovp", "vb", "festpreis",
}

SYNONYMS = {
    "playstation": "ps",
    "playstation5": "ps5",
    "playstation4": "ps4",
    "iphone": "iphone",
    "mac book": "macbook",
}


@dataclass
class ExportRow:
    category: str
    title: str
    price_text: str
    posted_text: str
    url: str
    first_seen_at: datetime
    price_eur: int | None = None


@dataclass
class FrequentRow:
    product_key: str
    example_title: str
    count: int
    min_price: int | None
    median_price: int | None
    max_price: int | None
    newest_posted: str
    example_url: str
    category: str


@dataclass
class MarketRow:
    category: str
    title: str
    price_text: str
    price_eur: int
    median_price: int
    discount_pct: int
    samples: int
    posted_text: str
    url: str
    first_seen_at: datetime


def _ascii(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def normalize_title(title: str) -> str:
    text = _ascii(title.lower())
    text = text.replace("playstation 5", "ps5").replace("playstation5", "ps5")
    text = text.replace("playstation 4", "ps4").replace("playstation4", "ps4")
    text = text.replace("x box", "xbox").replace("mac book", "macbook")
    text = re.sub(r"[^a-z0-9äöüß]+", " ", text)
    tokens = [t for t in text.split() if t and t not in TITLE_STOPWORDS]
    return " ".join(tokens)


def smart_duplicate_key(row: Listing | ExportRow) -> tuple[str, str, str]:
    # Same normalized title + effective price + category is treated as a likely repost.
    # If a numeric price exists, ignore harmless text differences such as "VB".
    title = normalize_title(row.title)
    price_text = getattr(row, "price_text", "") or ""
    price_eur = getattr(row, "price_eur", None)
    category = getattr(row, "category", "") or ""
    price_marker = f"eur:{price_eur}" if price_eur is not None else f"txt:{price_text.lower().strip()}"
    return category.lower(), title, price_marker


def product_family_key(title: str) -> str:
    """Heuristic product family for frequency/market analytics.

    It intentionally stays conservative. We use up to the first 5 meaningful
    normalized tokens, preserving model/storage numbers. This is a beta grouping,
    not ML classification.
    """
    norm = normalize_title(title)
    tokens = norm.split()
    if not tokens:
        return ""
    # Generic terms add little grouping value.
    generic = {"konsole", "controller", "set", "bundle", "zubehor", "zubehoer", "gerat", "geraet"}
    meaningful = [t for t in tokens if t not in generic]
    use = meaningful or tokens
    return " ".join(use[:5])


def posted_datetime(row: Listing | ExportRow) -> datetime:
    text = (getattr(row, "posted_text", None) or "").strip()
    now = datetime.now(BERLIN)
    m = re.search(r"Heute\s*,?\s*(\d{1,2}):(\d{2})", text, flags=re.IGNORECASE)
    if m:
        return now.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
    # first_seen_at is stored UTC-naive in this project.
    first_seen = getattr(row, "first_seen_at", None)
    if first_seen:
        return first_seen.replace(tzinfo=ZoneInfo("UTC")).astimezone(BERLIN)
    return now


def _contains_any(title: str, words: Iterable[str]) -> bool:
    norm = normalize_title(title)
    hay = f" {norm} "
    for raw in words:
        word = normalize_title(raw).strip()
        if word and f" {word} " in hay:
            return True
    return False


def period_cutoff(period: str) -> datetime | None:
    now = datetime.now(BERLIN)
    if period == "1h":
        return now - timedelta(hours=1)
    if period == "3h":
        return now - timedelta(hours=3)
    if period == "6h":
        return now - timedelta(hours=6)
    return None


def price_bounds(price_filter: str) -> tuple[int | None, int | None]:
    mapping = {
        "any": (None, None),
        "0_50": (0, 50),
        "50_100": (50, 100),
        "100_200": (100, 200),
        "200_500": (200, 500),
        "500_plus": (500, None),
    }
    return mapping.get(price_filter, (None, None))


def base_filter(
    rows: list[Listing],
    *,
    period: str,
    price_filter: str,
    clean_noise: bool,
    include_words: str,
    exclude_words: str,
) -> list[Listing]:
    cutoff = period_cutoff(period)
    min_price, max_price = price_bounds(price_filter)
    include = [x.strip() for x in include_words.split(",") if x.strip()]
    exclude = [x.strip() for x in exclude_words.split(",") if x.strip()]

    out: list[Listing] = []
    for row in rows:
        if cutoff and posted_datetime(row) < cutoff:
            continue
        if min_price is not None and (row.price_eur is None or row.price_eur < min_price):
            continue
        if max_price is not None and (row.price_eur is None or row.price_eur > max_price):
            continue
        if clean_noise and _contains_any(row.title, DEFAULT_NOISE_WORDS):
            continue
        if include and not _contains_any(row.title, include):
            continue
        if exclude and _contains_any(row.title, exclude):
            continue
        out.append(row)
    return out


def dedupe_rows(rows: list[Listing]) -> list[Listing]:
    # Sort newest first, then keep the newest probable repost.
    ordered = sorted(rows, key=posted_datetime, reverse=True)
    seen: set[tuple[str, str, str]] = set()
    result: list[Listing] = []
    for row in ordered:
        key = smart_duplicate_key(row)
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def sort_rows(rows: list[Listing], sort_mode: str) -> list[Listing]:
    if sort_mode == "price_asc":
        return sorted(rows, key=lambda r: (r.price_eur is None, r.price_eur or 0, -posted_datetime(r).timestamp()))
    if sort_mode == "price_desc":
        return sorted(rows, key=lambda r: (r.price_eur is None, -(r.price_eur or 0), -posted_datetime(r).timestamp()))
    return sorted(rows, key=posted_datetime, reverse=True)


def unique_rows(rows: list[Listing]) -> list[Listing]:
    groups: dict[tuple[str, str], list[Listing]] = defaultdict(list)
    for row in rows:
        groups[(row.category, product_family_key(row.title))].append(row)
    # "Unique" = family occurs only once in the selected period.
    return [items[0] for items in groups.values() if len(items) == 1]


def frequent_rows(rows: list[Listing], min_count: int = 2) -> list[FrequentRow]:
    groups: dict[tuple[str, str], list[Listing]] = defaultdict(list)
    for row in rows:
        key = product_family_key(row.title)
        if key:
            groups[(row.category, key)].append(row)

    output: list[FrequentRow] = []
    for (category, key), items in groups.items():
        if len(items) < min_count:
            continue
        prices = [x.price_eur for x in items if x.price_eur is not None]
        newest = max(items, key=posted_datetime)
        output.append(FrequentRow(
            product_key=key,
            example_title=newest.title,
            count=len(items),
            min_price=min(prices) if prices else None,
            median_price=int(statistics.median(prices)) if prices else None,
            max_price=max(prices) if prices else None,
            newest_posted=newest.posted_text or "Сегодня",
            example_url=newest.url,
            category=category,
        ))
    return sorted(output, key=lambda x: (-x.count, x.category, x.product_key))


def below_market_rows(rows: list[Listing], discount_threshold: float = 0.20, min_samples: int = 3) -> list[MarketRow]:
    groups: dict[tuple[str, str], list[Listing]] = defaultdict(list)
    for row in rows:
        if row.price_eur is None or row.price_eur <= 0:
            continue
        key = product_family_key(row.title)
        if key:
            groups[(row.category, key)].append(row)

    output: list[MarketRow] = []
    for (_category, _key), items in groups.items():
        prices = [x.price_eur for x in items if x.price_eur is not None and x.price_eur > 0]
        if len(prices) < min_samples:
            continue
        median = int(statistics.median(prices))
        if median <= 0:
            continue
        for row in items:
            if row.price_eur is None:
                continue
            discount = 1 - (row.price_eur / median)
            if discount >= discount_threshold:
                output.append(MarketRow(
                    category=row.category,
                    title=row.title,
                    price_text=row.price_text or f"{row.price_eur} €",
                    price_eur=row.price_eur,
                    median_price=median,
                    discount_pct=round(discount * 100),
                    samples=len(prices),
                    posted_text=row.posted_text or "Сегодня",
                    url=row.url,
                    first_seen_at=row.first_seen_at,
                ))
    return sorted(output, key=lambda x: (-x.discount_pct, -posted_datetime(x).timestamp()))
