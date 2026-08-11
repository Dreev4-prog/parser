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
from product_identity import recognize_product

BERLIN = ZoneInfo("Europe/Berlin")

DEFAULT_NOISE_WORDS = {
    "suche", "gesucht", "ankauf", "kaufe", "kaufen", "reparatur",
    "repariere", "service", "dienstleistung", "vermietung", "verleih",
    "tausche", "tausch", "wanted",
}

# Words that describe the ad rather than the product itself. Removing these makes
# family grouping much more stable across sellers.
TITLE_STOPWORDS = {
    "verkaufe", "verkauft", "biete", "angebot", "original", "top", "sehr",
    "guter", "gute", "gutes", "guten", "zustand", "neu", "neuwertig",
    "gebraucht", "inkl", "inklusive", "mit", "ohne", "und", "oder", "der",
    "die", "das", "ein", "eine", "einer", "einen", "für", "fuer", "von",
    "zu", "zum", "zur", "aus", "ovp", "vb", "festpreis", "verkauf",
    "privat", "abholung", "versand", "moglich", "moeglich", "sofort", "edition",
    "wie", "auf", "bei", "im", "in", "an", "nur", "noch", "sehr",
    "super", "perfekt", "funktioniert", "funktionierend", "voll",
    "wifi", "wi-fi", "wlan", "ethernet", "lieferung", "rechnung", "garantie",
}

# Cosmetic attributes usually should not split one model into many families.
COLOR_WORDS = {
    "schwarz", "black", "weiss", "weis", "white", "silber", "silver",
    "grau", "grey", "gray", "blau", "blue", "rot", "red", "grun", "green",
    "gruen", "gelb", "yellow", "pink", "rosa", "gold", "golden", "lila",
    "violett", "beige", "braun", "brown", "orange", "space", "spacegrau",
}

# Common words that change condition, not identity.
CONDITION_WORDS = {
    "defekt", "kaputt", "beschadigt", "beschaedigt", "ungepruft", "ungeprueft",
    "unbenutzt", "originalverpackt", "geoffnet", "geoeffnet", "kratzer",
    "gebrauchsspuren", "bware", "b-ware", "restposten",
}

# Product-type words that DO matter. They prevent, for example, a PS5 console
# and a PS5 controller from being merged into one family.
PRODUCT_TYPE_WORDS = {
    "controller", "gamepad", "headset", "dock", "dockingstation", "ladestation",
    "konsole", "console", "monitor", "fernseher", "tv", "kamera", "camera",
    "objektiv", "lens", "notebook", "laptop", "tablet", "smartphone", "handy",
    "drucker", "printer", "router", "receiver", "lautsprecher", "speaker",
    "soundbar", "beamer", "projektor", "uhr", "watch", "kopfhorer", "kopfhorer",
    "kopfhörer", "earbuds", "keyboard", "tastatur", "maus", "mouse",
}

# Variant words are kept because they are price-relevant model differences.
VARIANT_WORDS = {
    "slim", "pro", "max", "mini", "plus", "ultra", "oled", "lite", "digital",
    "disc", "disk", "edge", "portal", "air", "studio", "classic",
    "fold", "flip", "fe", "se", "xl", "seriesx", "seriess", "elite",
}

BRANDS = {
    "apple", "sony", "nintendo", "microsoft", "samsung", "google", "meta",
    "valve", "lenovo", "dell", "asus", "acer", "hp", "huawei", "xiaomi",
    "oneplus", "oppo", "motorola", "canon", "nikon", "fujifilm", "panasonic",
    "lg", "philips", "bose", "jbl", "sennheiser", "dyson", "bosch", "makita",
}

STRONG_ROOTS = {
    "ps5", "ps4", "ps3", "psportal", "xbox", "seriesx", "seriess", "switch",
    "iphone", "ipad", "macbook", "imac", "macmini", "airpods", "appletv",
    "galaxy", "pixel", "steamdeck", "metaquest", "quest", "surface", "dualsense",
    "thinkpad", "ideapad", "legion", "vivobook", "zenbook", "rog",
}

REDUNDANT_BRAND_BY_ROOT = {
    "sony": {"ps5", "ps4", "ps3", "psportal"},
    "nintendo": {"switch"},
    "microsoft": {"xbox", "seriesx", "seriess", "surface"},
    "apple": {"iphone", "ipad", "macbook", "imac", "macmini", "airpods", "appletv"},
    "samsung": {"galaxy"},
    "google": {"pixel"},
    "valve": {"steamdeck"},
    "meta": {"metaquest", "quest"},
}


@dataclass(frozen=True)
class ProductFamily:
    key: str
    confidence: int  # 0..100


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
    confidence: int = 0


@dataclass
class MarketRow:
    category: str
    title: str
    price_text: str
    price_eur: int
    view_count: int | None
    median_price: int
    discount_pct: int
    samples: int
    posted_text: str
    url: str
    first_seen_at: datetime
    confidence: int = 0


@dataclass
class DisappearingRow:
    category: str
    title: str
    price_text: str
    lifespan_minutes: int
    first_seen_at: datetime
    disappeared_at: datetime
    url: str
    detection_gap_minutes: int = 0
    confidence: str = "низкая"


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
    text = text.replace("ß", "ss").replace("ẞ", "SS")
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _canonical_text(title: str) -> str:
    text = _ascii(title.lower())
    replacements = [
        (r"\bplay\s*station\s*5\b", "ps5"),
        (r"\bplay\s*station\s*4\b", "ps4"),
        (r"\bplay\s*station\s*3\b", "ps3"),
        (r"\bplay\s*station\s*portal\b", "psportal"),
        (r"\bps\s*5\b", "ps5"),
        (r"\bps\s*4\b", "ps4"),
        (r"\bps\s*3\b", "ps3"),
        (r"\bx\s*box\b", "xbox"),
        (r"\bxbox\s+series\s+x\b", "xbox seriesx"),
        (r"\bxbox\s+series\s+s\b", "xbox seriess"),
        (r"\bmac\s*book\b", "macbook"),
        (r"\bmac\s+mini\b", "macmini"),
        (r"\bair\s*pods\b", "airpods"),
        (r"\bapple\s+tv\b", "appletv"),
        (r"\bsteam\s+deck\b", "steamdeck"),
        (r"\bmeta\s+quest\b", "metaquest"),
        (r"\bnintendo\s+switch\b", "nintendo switch"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)

    # Storage and generation notation varies heavily between sellers.
    text = re.sub(r"\b(\d+)\s*(gb|tb)\b", r"\1\2", text)
    text = re.sub(r"\b(\d+)\s*\.?\s*(?:generation|gen\.?|generationen)\b", r"gen\1", text)
    text = re.sub(r"\bgen\.?\s*(\d+)\b", r"gen\1", text)
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def normalize_title(title: str) -> str:
    tokens = []
    for token in _canonical_text(title).split():
        if token in TITLE_STOPWORDS:
            continue
        tokens.append(token)
    return " ".join(tokens)


def _identity_tokens(title: str) -> list[str]:
    raw = normalize_title(title).split()
    tokens: list[str] = []
    for token in raw:
        if token in COLOR_WORDS or token in CONDITION_WORDS:
            continue
        # Quantity markers like 2x/3x are usually bundle detail rather than model.
        if re.fullmatch(r"\d+x", token):
            continue
        tokens.append(token)

    # Drop a brand if the model root already makes that brand unambiguous.
    token_set = set(tokens)
    for brand, roots in REDUNDANT_BRAND_BY_ROOT.items():
        if brand in token_set and token_set.intersection(roots):
            tokens = [t for t in tokens if t != brand]
            token_set.discard(brand)
    return tokens


def _is_modelish(token: str) -> bool:
    if token in STRONG_ROOTS or token in VARIANT_WORDS or token in PRODUCT_TYPE_WORDS:
        return True
    if re.search(r"\d", token):
        return True
    return False


def product_family(title: str) -> ProductFamily:
    # v3.0: use the structured recognizer first. This preserves price-relevant
    # differences such as PS5 Slim Disc vs Digital or iPhone storage sizes.
    identity = recognize_product(title)
    if identity.key and identity.confidence >= 70:
        return ProductFamily(identity.key, identity.confidence)

    # Conservative fallback for categories/models not covered by the explicit
    # recognizer yet. Unknown titles are not forced into a fake strong identity.
    tokens = _identity_tokens(title)
    if not tokens:
        return ProductFamily("", 0)

    modelish = [t for t in tokens if _is_modelish(t)]
    lexical = [t for t in tokens if t not in modelish and t not in BRANDS]
    brands = [t for t in tokens if t in BRANDS]

    if modelish:
        # Model/variant tokens are strong anchors. Keep at most one brand and two
        # extra lexical words so accessory/model distinctions survive without
        # seller prose fragmenting the family.
        chosen = modelish + brands[:1] + lexical[:2]
    else:
        # Generic categories (furniture, books, hobby, etc.) still get a stable
        # conservative key. This intentionally groups only very similar titles.
        chosen = brands[:1] + lexical[:5]

    # Bag-of-identity tokens makes word order irrelevant across sellers.
    key_tokens = sorted(set(chosen))
    key = " ".join(key_tokens)

    confidence = 25
    if any(t in STRONG_ROOTS for t in key_tokens):
        confidence += 35
    if any(re.search(r"\d", t) for t in key_tokens):
        confidence += 20
    if any(t in VARIANT_WORDS or t in PRODUCT_TYPE_WORDS for t in key_tokens):
        confidence += 10
    if len(key_tokens) >= 2:
        confidence += 10
    if not modelish and len(key_tokens) < 2:
        confidence -= 20
    return ProductFamily(key, max(0, min(100, confidence)))


def product_family_key(title: str) -> str:
    return product_family(title).key


def smart_duplicate_key(row: Listing | ExportRow) -> tuple[str, str, str]:
    # Duplicate detection stays deliberately stricter than family grouping.
    # Otherwise two real sellers listing the same model for the same price would
    # be collapsed incorrectly.
    title = normalize_title(row.title)
    price_text = getattr(row, "price_text", "") or ""
    price_eur = getattr(row, "price_eur", None)
    category = getattr(row, "category", "") or ""
    price_marker = f"eur:{price_eur}" if price_eur is not None else f"txt:{price_text.lower().strip()}"
    return category.lower(), title, price_marker


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


def _family_groups(rows: list[Listing]) -> dict[tuple[str, str], list[Listing]]:
    groups: dict[tuple[str, str], list[Listing]] = defaultdict(list)
    for row in rows:
        family = product_family(row.title)
        if family.key:
            groups[(row.category, family.key)].append(row)
    return groups


def unique_rows(rows: list[Listing]) -> list[Listing]:
    """Return genuinely rare families in the current filtered dataset.

    A family must occur exactly once. Grouping is order-insensitive and model-aware,
    so cosmetic seller wording does not turn common products into fake uniques.
    """
    groups = _family_groups(rows)
    result: list[Listing] = []
    for (_category, key), items in groups.items():
        if len(items) != 1:
            continue
        family = product_family(items[0].title)
        # Very weak one-word generic families are not useful as "unique" signals.
        if family.confidence < 35:
            continue
        result.append(items[0])
    return result


def frequent_rows(rows: list[Listing], min_count: int = 3) -> list[FrequentRow]:
    groups = _family_groups(rows)
    output: list[FrequentRow] = []
    for (category, key), items in groups.items():
        # DB has unique external_id, but preserve this guarantee if rows are ever
        # assembled from another source in future.
        unique_by_id = {x.external_id: x for x in items}
        items = list(unique_by_id.values())
        if len(items) < min_count:
            continue
        family = product_family(items[0].title)
        prices = [x.price_eur for x in items if x.price_eur is not None and x.price_eur > 0]
        newest = max(items, key=posted_datetime)
        identity = recognize_product(newest.title, newest.category)
        readable_key = identity.label if identity.key and identity.confidence >= 70 else key
        output.append(FrequentRow(
            product_key=readable_key,
            example_title=newest.title,
            example_price_text=newest.price_text or (f"{newest.price_eur} €" if newest.price_eur is not None else ""),
            count=len(items),
            min_price=min(prices) if prices else None,
            median_price=int(statistics.median(prices)) if prices else None,
            max_price=max(prices) if prices else None,
            newest_posted=newest.posted_text or "Сегодня",
            example_url=newest.url,
            category=category,
            confidence=family.confidence,
        ))
    return sorted(output, key=lambda x: (-x.count, -x.confidence, x.category, x.product_key))


def below_market_rows(rows: list[Listing], discount_threshold: float = 0.20, min_samples: int = 5) -> list[MarketRow]:
    """Find price outliers against similar products using leave-one-out median.

    The candidate itself is excluded from the comparison median, which prevents a
    very cheap listing from pulling its own benchmark down. At least 5 total priced
    examples are required for a stronger signal than v2.3.
    """
    groups = _family_groups(rows)
    output: list[MarketRow] = []
    for (_category, _key), items in groups.items():
        priced = [x for x in items if x.price_eur is not None and x.price_eur > 0]
        if len(priced) < min_samples:
            continue
        family = product_family(priced[0].title)
        for row in priced:
            peers = [x.price_eur for x in priced if x.external_id != row.external_id and x.price_eur is not None]
            if len(peers) < min_samples - 1:
                continue
            median = int(statistics.median(peers))
            if median <= 0 or row.price_eur is None:
                continue
            # Ignore obvious bait/placeholder values (e.g. 1 € for a 500 € family).
            if median >= 50 and row.price_eur < max(5, int(median * 0.10)):
                continue
            discount = 1 - (row.price_eur / median)
            if discount >= discount_threshold:
                output.append(MarketRow(
                    category=row.category,
                    title=row.title,
                    price_text=row.price_text or f"{row.price_eur} €",
                    price_eur=row.price_eur,
                    view_count=getattr(row, "view_count", None),
                    median_price=median,
                    discount_pct=round(discount * 100),
                    samples=len(priced),
                    posted_text=row.posted_text or "Сегодня",
                    url=row.url,
                    first_seen_at=row.first_seen_at,
                    confidence=family.confidence,
                ))
    return sorted(output, key=lambda x: (-x.discount_pct, -x.confidence, -posted_datetime(x).timestamp()))


def disappearing_rows(rows: list[Listing], max_lifespan_hours: int = 12) -> list[DisappearingRow]:
    """Return disappeared ads, prioritising short and well-observed lifetimes.

    We cannot prove a disappearance means a sale. Confidence is based on how soon
    after the last known-live observation the disappearance was detected.
    """
    output: list[DisappearingRow] = []
    max_minutes = max_lifespan_hours * 60
    for row in rows:
        if not row.disappeared_at:
            continue
        delta = row.disappeared_at - row.first_seen_at
        minutes = max(0, int(delta.total_seconds() // 60))
        if minutes > max_minutes:
            continue
        last_seen = row.last_seen_at or row.first_seen_at
        gap = max(0, int((row.disappeared_at - last_seen).total_seconds() // 60))
        if gap <= 30:
            confidence = "высокая"
        elif gap <= 120:
            confidence = "средняя"
        else:
            confidence = "низкая"
        output.append(DisappearingRow(
            category=row.category,
            title=row.title,
            price_text=row.price_text or (f"{row.price_eur} €" if row.price_eur is not None else ""),
            lifespan_minutes=minutes,
            first_seen_at=row.first_seen_at,
            disappeared_at=row.disappeared_at,
            url=row.url,
            detection_gap_minutes=gap,
            confidence=confidence,
        ))
    rank = {"высокая": 0, "средняя": 1, "низкая": 2}
    return sorted(output, key=lambda x: (rank.get(x.confidence, 9), x.lifespan_minutes, x.detection_gap_minutes))


def price_drop_rows(
    rows: list[Listing],
    histories: list[PriceHistory],
    min_drop_pct: int = 5,
    min_drop_eur: int = 5,
) -> list[PriceDropRow]:
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
        if drop < min_drop_eur or pct < min_drop_pct:
            continue
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
