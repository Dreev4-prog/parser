from __future__ import annotations

import re
import statistics
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable
from zoneinfo import ZoneInfo

from models import Listing, PriceHistory

BERLIN = ZoneInfo("Europe/Berlin")

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
    example_price_text: str
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


@dataclass
class DisappearingRow:
    category: str
    title: str
    price_text: str
    lifespan_minutes: int
    first_seen_at: datetime
    disappeared_at: datetime
    url: str


@dataclass
class PriceDropRow:
    category: str
    title: str
    previous_price: int
    current_price: int
    drop_eur: int
    drop_pct: int
    changed_at: datetime
    url: str


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
    title = normalize_title(row.title)
    price_text = getattr(row, "price_text", "") or ""
    price_eur = getattr(row, "price_eur", None)
    category = getattr(row, "category", "") or ""
    price_marker = f"eur:{price_eur}" if price_eur is not None else f"txt:{price_text.lower().strip()}"
    return category.lower(), title, price_marker


def product_family_key(title: str) -> str:
    norm = normalize_title(title)
    tokens = norm.split()
    if not tokens:
        return ""
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
            example_price_text=newest.price_text or (f"{newest.price_eur} €" if newest.price_eur is not None else ""),
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


def disappearing_rows(rows: list[Listing]) -> list[DisappearingRow]:
    output: list[DisappearingRow] = []
    for row in rows:
        if not row.disappeared_at:
            continue
        delta = row.disappeared_at - row.first_seen_at
        minutes = max(0, int(delta.total_seconds() // 60))
        output.append(DisappearingRow(
            category=row.category,
            title=row.title,
            price_text=row.price_text or (f"{row.price_eur} €" if row.price_eur is not None else ""),
            lifespan_minutes=minutes,
            first_seen_at=row.first_seen_at,
            disappeared_at=row.disappeared_at,
            url=row.url,
        ))
    return sorted(output, key=lambda x: (x.lifespan_minutes, x.disappeared_at))


def price_drop_rows(rows: list[Listing], histories: list[PriceHistory]) -> list[PriceDropRow]:
    listings = {r.external_id: r for r in rows}
    grouped: dict[str, list[PriceHistory]] = defaultdict(list)
    for h in histories:
        if h.external_id in listings and h.price_eur is not None and h.price_eur > 0:
            grouped[h.external_id].append(h)

    output: list[PriceDropRow] = []
    for external_id, events in grouped.items():
        events = sorted(events, key=lambda h: h.recorded_at)
        compressed: list[PriceHistory] = []
        for event in events:
            if not compressed or compressed[-1].price_eur != event.price_eur:
                compressed.append(event)
        if len(compressed) < 2:
            continue
        previous, current = compressed[-2], compressed[-1]
        if current.price_eur is None or previous.price_eur is None or current.price_eur >= previous.price_eur:
            continue
        row = listings[external_id]
        drop = previous.price_eur - current.price_eur
        pct = round(drop / previous.price_eur * 100) if previous.price_eur else 0
        output.append(PriceDropRow(
            category=row.category,
            title=row.title,
            previous_price=previous.price_eur,
            current_price=current.price_eur,
            drop_eur=drop,
            drop_pct=pct,
            changed_at=current.recorded_at,
            url=row.url,
        ))
    return sorted(output, key=lambda x: (-x.drop_pct, -x.drop_eur, x.title.lower()))
