from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


@dataclass(frozen=True)
class ProductIdentity:
    """Normalized product identity extracted from a marketplace title.

    The key is intentionally price-relevant: model/variant/storage/RAM differences
    are preserved when we are confident enough to detect them.
    """

    brand: str = ""
    product_type: str = ""
    model: str = ""
    variant: str = ""
    storage_gb: int | None = None
    ram_gb: int | None = None
    specs: str = ""
    key: str = ""
    label: str = ""
    confidence: int = 0


BRAND_DISPLAY = {
    "apple": "Apple",
    "sony": "Sony",
    "nintendo": "Nintendo",
    "microsoft": "Microsoft",
    "valve": "Valve",
    "meta": "Meta",
    "samsung": "Samsung",
    "google": "Google",
    "nvidia": "NVIDIA",
    "amd": "AMD",
    "intel": "Intel",
    "asus": "ASUS",
    "acer": "Acer",
    "lenovo": "Lenovo",
    "dell": "Dell",
    "hp": "HP",
    "huawei": "Huawei",
    "xiaomi": "Xiaomi",
    "oneplus": "OnePlus",
    "oppo": "OPPO",
    "motorola": "Motorola",
    "canon": "Canon",
    "nikon": "Nikon",
    "fujifilm": "Fujifilm",
    "panasonic": "Panasonic",
    "lg": "LG",
    "philips": "Philips",
    "bose": "Bose",
    "jbl": "JBL",
    "sennheiser": "Sennheiser",
    "msi": "MSI",
    "gigabyte": "Gigabyte",
    "zotac": "Zotac",
    "sapphire": "Sapphire",
    "powercolor": "PowerColor",
}

KNOWN_BRANDS = tuple(BRAND_DISPLAY)

TYPE_DISPLAY = {
    "phone": "смартфон",
    "tablet": "планшет",
    "laptop": "ноутбук",
    "desktop": "компьютер",
    "streamer": "медиаплеер",
    "headphones": "наушники",
    "console": "консоль",
    "handheld": "портативная консоль",
    "controller": "контроллер",
    "accessory": "аксессуар",
    "gpu": "видеокарта",
    "cpu": "процессор",
    "camera": "камера",
    "watch": "часы",
    "monitor": "монитор",
    "audio": "аудио",
    "device": "устройство",
}


def _ascii(text: str) -> str:
    text = text.replace("ß", "ss").replace("ẞ", "SS")
    text = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def canonical_text(title: str) -> str:
    text = _ascii((title or "").lower())
    replacements = [
        (r"\bplay\s*station\s*portal\b", "ps portal"),
        (r"\bplay\s*station\s*5\b", "ps5"),
        (r"\bplay\s*station\s*4\b", "ps4"),
        (r"\bps\s*5\b", "ps5"),
        (r"\bps\s*4\b", "ps4"),
        (r"\bx\s*box\b", "xbox"),
        (r"\bmac\s*book\b", "macbook"),
        (r"\bmac\s+mini\b", "mac mini"),
        (r"\bair\s*pods\b", "airpods"),
        (r"\bapple\s+tv\b", "apple tv"),
        (r"\bsteam\s+deck\b", "steam deck"),
        (r"\bmeta\s+quest\b", "meta quest"),
    ]
    for pattern, replacement in replacements:
        text = re.sub(pattern, replacement, text)
    text = text.replace("+", " plus ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _slug(value: str) -> str:
    value = canonical_text(value)
    value = re.sub(r"[^a-z0-9]+", "-", value).strip("-")
    return value


def _memory_values(text: str) -> list[tuple[int, int, int, str]]:
    values: list[tuple[int, int, int, str]] = []
    for match in re.finditer(r"(?<!\d)(\d+(?:[\.,]\d+)?)\s*(tb|gb)\b", text):
        number = float(match.group(1).replace(",", "."))
        gb = int(round(number * 1000)) if match.group(2) == "tb" else int(round(number))
        values.append((gb, match.start(), match.end(), match.group(0)))
    return values


def _has_ram_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 24):start]
    after = text[end:min(len(text), end + 20)]
    # Common form: "16GB RAM" / "16 GB Arbeitsspeicher".
    if re.match(r"\s*(?:ram|arbeitsspeicher|memory|ddr[345]?)\b", after):
        return True
    # "RAM 16GB" is also common, but avoid stealing the storage value in
    # strings like "8GB RAM 256GB" where RAM belongs to the previous number.
    if re.search(r"(?:ram|arbeitsspeicher|memory|ddr[345]?)\s*[:/-]?\s*$", before):
        if re.search(r"\d+(?:[\.,]\d+)?\s*(?:gb|tb)\s+(?:ram|arbeitsspeicher|memory|ddr[345]?)\s*[:/-]?\s*$", before):
            return False
        return True
    return False


def _has_storage_context(text: str, start: int, end: int) -> bool:
    before = text[max(0, start - 18):start]
    after = text[end:min(len(text), end + 18)]
    return bool(
        re.match(r"\s*(?:ssd|speicher|storage|festplatte|nvme)\b", after)
        or re.search(r"(?:ssd|speicher|storage|festplatte|nvme)\s*[:/-]?\s*$", before)
    )


def _pick_storage(text: str, *, min_gb: int = 32, max_gb: int = 8000) -> int | None:
    values = _memory_values(text)
    candidates: list[int] = []
    for gb, start, end, _ in values:
        if not (min_gb <= gb <= max_gb):
            continue
        if _has_ram_context(text, start, end) and not _has_storage_context(text, start, end):
            continue
        candidates.append(gb)
    return max(candidates) if candidates else None


def _pick_ram(text: str) -> int | None:
    explicit: list[int] = []
    for gb, start, end, _ in _memory_values(text):
        if 2 <= gb <= 128 and _has_ram_context(text, start, end):
            explicit.append(gb)
    if explicit:
        return max(explicit)
    return None


def _memory_label(gb: int | None) -> str:
    if gb is None:
        return ""
    if gb >= 1000 and gb % 1000 == 0:
        return f"{gb // 1000} TB"
    if gb > 1000:
        return f"{gb / 1000:g} TB"
    return f"{gb} GB"


def _compose(
    *,
    brand: str,
    product_type: str,
    model: str,
    variant_parts: list[str] | tuple[str, ...] = (),
    storage_gb: int | None = None,
    ram_gb: int | None = None,
    spec_parts: list[str] | tuple[str, ...] = (),
    confidence: int,
) -> ProductIdentity:
    brand_display = BRAND_DISPLAY.get(brand.lower(), brand.strip())
    variants = [v.strip() for v in variant_parts if v and v.strip()]
    specs = [v.strip() for v in spec_parts if v and v.strip()]
    variant = " ".join(dict.fromkeys(variants))
    specs_text = " · ".join(dict.fromkeys(specs))

    key_parts = [
        _slug(brand_display),
        _slug(product_type),
        _slug(model),
        _slug(variant),
    ]
    if ram_gb is not None:
        key_parts.append(f"ram-{ram_gb}gb")
    if storage_gb is not None:
        key_parts.append(f"storage-{storage_gb}gb")
    # Specs such as display size/chip are model-defining and belong in the key.
    for spec in specs:
        key_parts.append(_slug(spec))
    key = "|".join(part for part in key_parts if part)

    label_parts = [model] if model.lower().startswith(brand_display.lower()) else [brand_display, model]
    if variant:
        label_parts.append(variant)
    if specs_text:
        label_parts.append(specs_text)
    if ram_gb is not None:
        label_parts.append(f"{ram_gb} GB RAM")
    if storage_gb is not None:
        label_parts.append(_memory_label(storage_gb))
    label = " · ".join(part for part in label_parts if part)

    return ProductIdentity(
        brand=brand_display,
        product_type=product_type,
        model=model,
        variant=variant,
        storage_gb=storage_gb,
        ram_gb=ram_gb,
        specs=specs_text,
        key=key,
        label=label,
        confidence=max(0, min(100, confidence)),
    )


def _generation(text: str) -> str:
    patterns = [
        r"\b(\d{1,2})\s*\.?\s*(?:generation|gen(?:eration)?\.?)\b",
        r"\bgen(?:eration)?\.?\s*(\d{1,2})\b",
    ]
    for pattern in patterns:
        m = re.search(pattern, text)
        if m:
            return f"Gen {int(m.group(1))}"
    return ""


def _screen_size(text: str) -> str:
    m = re.search(r"\b(1[1-7](?:[\.,]\d)?)\s*(?:zoll|inch|\")", text)
    if not m:
        return ""
    value = m.group(1).replace(",", ".")
    return f"{value}\""


def _apple(text: str) -> ProductIdentity | None:
    if "iphone" in text:
        m = re.search(r"\biphone\s*(se(?:\s*[123])?|x(?:s|r)?|(?:[6-9]|1\d)(?:e)?)\b", text)
        if not m:
            return _compose(brand="apple", product_type="phone", model="iPhone", confidence=58)
        raw_model = re.sub(r"\s+", " ", m.group(1).upper() if m.group(1).startswith("x") else m.group(1))
        if raw_model.lower().startswith("se"):
            raw_model = "SE" + raw_model[2:]
        model = f"iPhone {raw_model}"
        variants: list[str] = []
        if re.search(r"\bpro\s+max\b", text):
            variants.append("Pro Max")
        elif re.search(r"\bpro\b", text):
            variants.append("Pro")
        elif re.search(r"\bplus\b", text):
            variants.append("Plus")
        elif re.search(r"\bmini\b", text):
            variants.append("Mini")
        storage = _pick_storage(text, min_gb=32, max_gb=2000)
        return _compose(
            brand="apple", product_type="phone", model=model, variant_parts=variants,
            storage_gb=storage, confidence=94 if storage else 90,
        )

    if re.search(r"\bipad\b", text):
        if re.search(r"\bipad\s+pro\b", text):
            model = "iPad Pro"
        elif re.search(r"\bipad\s+air\b", text):
            model = "iPad Air"
        elif re.search(r"\bipad\s+mini\b", text):
            model = "iPad mini"
        else:
            model = "iPad"
        gen = _generation(text)
        screen = _screen_size(text)
        chip_match = re.search(r"\b(m[1-6])\b", text)
        specs = [x for x in (screen, chip_match.group(1).upper() if chip_match else "", gen) if x]
        storage = _pick_storage(text, min_gb=32, max_gb=4000)
        return _compose(
            brand="apple", product_type="tablet", model=model, storage_gb=storage,
            spec_parts=specs, confidence=92 if (storage or specs) else 84,
        )

    if "macbook" in text:
        model = "MacBook"
        if re.search(r"\bmacbook\s+air\b", text):
            model = "MacBook Air"
        elif re.search(r"\bmacbook\s+pro\b", text):
            model = "MacBook Pro"
        chip = ""
        chip_m = re.search(r"\b(m[1-6])(?:\s+(pro|max|ultra))?\b", text)
        if chip_m:
            chip = chip_m.group(1).upper() + (f" {chip_m.group(2).title()}" if chip_m.group(2) else "")
        screen = _screen_size(text)
        ram = _pick_ram(text)
        values = _memory_values(text)
        if ram is None and len(values) >= 2:
            small = [gb for gb, *_ in values if 4 <= gb <= 128]
            large = [gb for gb, *_ in values if gb >= 128]
            if small and large:
                ram = min(small)
        storage = _pick_storage(text, min_gb=128, max_gb=8000)
        return _compose(
            brand="apple", product_type="laptop", model=model, storage_gb=storage, ram_gb=ram,
            spec_parts=[x for x in (screen, chip) if x], confidence=96 if chip else 86,
        )

    if re.search(r"\bapple\s+tv\b", text):
        model = "Apple TV"
        if re.search(r"\b4k\b", text):
            model = "Apple TV 4K"
        elif re.search(r"\bhd\b", text):
            model = "Apple TV HD"
        gen = _generation(text)
        variants: list[str] = []
        if "ethernet" in text:
            variants.append("Ethernet")
        storage = _pick_storage(text, min_gb=16, max_gb=512)
        return _compose(
            brand="apple", product_type="streamer", model=model, variant_parts=variants,
            storage_gb=storage, spec_parts=[gen] if gen else [], confidence=96 if "4K" in model else 88,
        )

    if "airpods" in text:
        model = "AirPods"
        if re.search(r"\bairpods\s+pro\b", text):
            model = "AirPods Pro"
        elif re.search(r"\bairpods\s+max\b", text):
            model = "AirPods Max"
        gen = _generation(text)
        return _compose(
            brand="apple", product_type="headphones", model=model,
            spec_parts=[gen] if gen else [], confidence=90,
        )

    if re.search(r"\bmac\s+mini\b", text):
        chip_m = re.search(r"\b(m[1-6])(?:\s+(pro|max))?\b", text)
        chip = chip_m.group(1).upper() + (f" {chip_m.group(2).title()}" if chip_m and chip_m.group(2) else "") if chip_m else ""
        ram = _pick_ram(text)
        storage = _pick_storage(text, min_gb=128, max_gb=8000)
        return _compose(
            brand="apple", product_type="desktop", model="Mac mini", storage_gb=storage, ram_gb=ram,
            spec_parts=[chip] if chip else [], confidence=92 if chip else 82,
        )

    if re.search(r"\bimac\b", text):
        chip_m = re.search(r"\b(m[1-6])\b", text)
        chip = chip_m.group(1).upper() if chip_m else ""
        ram = _pick_ram(text)
        storage = _pick_storage(text, min_gb=128, max_gb=8000)
        return _compose(
            brand="apple", product_type="desktop", model="iMac", storage_gb=storage, ram_gb=ram,
            spec_parts=[x for x in (_screen_size(text), chip) if x], confidence=88,
        )
    return None


def _playstation(text: str) -> ProductIdentity | None:
    if re.search(r"\b(ps\s+portal|psportal|playstation\s+portal)\b", text):
        return _compose(brand="sony", product_type="handheld", model="PlayStation Portal", confidence=98)

    if re.search(r"\bdualsense\s+edge\b", text):
        return _compose(brand="sony", product_type="controller", model="DualSense Edge", confidence=99)
    if re.search(r"\bdualsense\b", text) or ("controller" in text and ("ps5" in text or "playstation" in text)):
        return _compose(brand="sony", product_type="controller", model="DualSense", confidence=92)

    # Standalone disc drive/accessory should not be merged with the console itself.
    if ("ps5" in text or "playstation 5" in text) and re.search(r"\b(laufwerk|disc\s+drive|disk\s+drive)\b", text):
        if re.search(r"\b(fur|fuer|for)\s+(?:die\s+)?(?:ps5|playstation)\b", text) or re.search(r"\bps5\s+(?:disc\s+)?laufwerk\b", text):
            return _compose(brand="sony", product_type="accessory", model="PS5 Disc Drive", confidence=93)

    for token, model in (("ps5", "PlayStation 5"), ("ps4", "PlayStation 4")):
        if token not in text:
            continue
        variants: list[str] = []
        if re.search(r"\bpro\b", text):
            variants.append("Pro")
        elif re.search(r"\bslim\b", text):
            variants.append("Slim")
        if re.search(r"\b(digital|ohne\s+laufwerk)\b", text):
            variants.append("Digital")
        elif re.search(r"\b(disc|disk|mit\s+laufwerk)\b", text):
            variants.append("Disc")
        storage = _pick_storage(text, min_gb=250, max_gb=8000)
        return _compose(
            brand="sony", product_type="console", model=model, variant_parts=variants,
            storage_gb=storage, confidence=98 if variants else 92,
        )
    return None


def _xbox(text: str) -> ProductIdentity | None:
    if not re.search(r"\bxbox\b", text):
        return None
    if "controller" in text or "gamepad" in text:
        return _compose(brand="microsoft", product_type="controller", model="Xbox Controller", confidence=88)
    model = "Xbox"
    if re.search(r"\bseries\s*x\b", text):
        model = "Xbox Series X"
    elif re.search(r"\bseries\s*s\b", text):
        model = "Xbox Series S"
    elif re.search(r"\bxbox\s+one\s+x\b", text):
        model = "Xbox One X"
    elif re.search(r"\bxbox\s+one\s+s\b", text):
        model = "Xbox One S"
    elif re.search(r"\bxbox\s+one\b", text):
        model = "Xbox One"
    storage = _pick_storage(text, min_gb=250, max_gb=8000)
    return _compose(brand="microsoft", product_type="console", model=model, storage_gb=storage, confidence=94 if model != "Xbox" else 70)


def _nintendo(text: str) -> ProductIdentity | None:
    if "switch" not in text:
        return None
    model = "Nintendo Switch"
    variants: list[str] = []
    if re.search(r"\bswitch\s*2\b", text):
        model = "Nintendo Switch 2"
    elif re.search(r"\boled\b", text):
        variants.append("OLED")
    elif re.search(r"\blite\b", text):
        variants.append("Lite")
    if re.search(r"\b(pro\s+)?controller\b", text):
        return _compose(brand="nintendo", product_type="controller", model="Switch Controller", variant_parts=variants, confidence=88)
    storage = _pick_storage(text, min_gb=32, max_gb=4000)
    return _compose(brand="nintendo", product_type="console", model=model, variant_parts=variants, storage_gb=storage, confidence=96)


def _steam_meta(text: str) -> ProductIdentity | None:
    if re.search(r"\bsteam\s+deck\b", text):
        variants = ["OLED"] if "oled" in text else []
        storage = _pick_storage(text, min_gb=64, max_gb=4000)
        return _compose(brand="valve", product_type="handheld", model="Steam Deck", variant_parts=variants, storage_gb=storage, confidence=98)
    m = re.search(r"\b(?:meta\s+)?quest\s*(3s|[123]|pro)?\b", text)
    if m and ("quest" in text):
        suffix = (m.group(1) or "").upper()
        model = f"Meta Quest {suffix}".strip()
        storage = _pick_storage(text, min_gb=64, max_gb=2000)
        return _compose(brand="meta", product_type="headset", model=model, storage_gb=storage, confidence=94 if suffix else 78)
    return None


def _samsung_google(text: str) -> ProductIdentity | None:
    if "galaxy" in text:
        m = re.search(r"\bgalaxy\s+(s\s*\d{2}|a\s*\d{2}|note\s*\d{1,2}|z\s*(?:fold|flip)\s*\d+)\b", text)
        if m:
            code = re.sub(r"\s+", "", m.group(1).upper())
            if code.startswith("Z"):
                code = re.sub(r"Z(FOLD|FLIP)", r"Z \1 ", code).strip().title().replace("Z ", "Z ")
            model = f"Galaxy {code}"
            variants: list[str] = []
            if re.search(r"\bultra\b", text): variants.append("Ultra")
            elif re.search(r"\bplus\b", text): variants.append("Plus")
            if re.search(r"\bfe\b", text): variants.append("FE")
            storage = _pick_storage(text, min_gb=32, max_gb=2000)
            return _compose(brand="samsung", product_type="phone", model=model, variant_parts=variants, storage_gb=storage, confidence=92)
    m = re.search(r"\bpixel\s*(\d{1,2}[a-z]?)\b", text)
    if m:
        model = f"Pixel {m.group(1).upper()}"
        variants: list[str] = []
        if re.search(r"\bpro\s+xl\b", text): variants.append("Pro XL")
        elif re.search(r"\bpro\b", text): variants.append("Pro")
        elif re.search(r"\bxl\b", text): variants.append("XL")
        elif re.search(r"\bfold\b", text): variants.append("Fold")
        storage = _pick_storage(text, min_gb=32, max_gb=2000)
        return _compose(brand="google", product_type="phone", model=model, variant_parts=variants, storage_gb=storage, confidence=94)
    return None


def _gpu_cpu(text: str) -> ProductIdentity | None:
    m = re.search(r"\b(rtx|gtx)\s*(\d{3,4})(?:\s*(ti))?(?:\s*(super))?\b", text)
    if m:
        model = f"{m.group(1).upper()} {m.group(2)}"
        variants = [x.title() for x in (m.group(3), m.group(4)) if x]
        return _compose(brand="nvidia", product_type="gpu", model=model, variant_parts=variants, confidence=98)
    m = re.search(r"\brx\s*(\d{4})(?:\s*(xtx|xt))?\b", text)
    if m:
        model = f"RX {m.group(1)}"
        variants = [m.group(2).upper()] if m.group(2) else []
        return _compose(brand="amd", product_type="gpu", model=model, variant_parts=variants, confidence=98)

    m = re.search(r"\bryzen\s*([3579])\s*(\d{4,5}[a-z0-9]{0,3})\b", text)
    if m:
        model = f"Ryzen {m.group(1)} {m.group(2).upper()}"
        return _compose(brand="amd", product_type="cpu", model=model, confidence=99)
    m = re.search(r"\b(?:intel\s+)?(?:core\s+)?i([3579])[-\s]?(\d{4,5}[a-z]{0,2})\b", text)
    if m:
        model = f"Core i{m.group(1)}-{m.group(2).upper()}"
        return _compose(brand="intel", product_type="cpu", model=model, confidence=99)
    m = re.search(r"\b(?:intel\s+)?core\s+ultra\s*([579])\s*(\d{3}[a-z]?)\b", text)
    if m:
        model = f"Core Ultra {m.group(1)} {m.group(2).upper()}"
        return _compose(brand="intel", product_type="cpu", model=model, confidence=99)
    return None


def _generic(text: str, category: str = "") -> ProductIdentity:
    brand = ""
    for candidate in KNOWN_BRANDS:
        if re.search(rf"\b{re.escape(candidate)}\b", text):
            brand = candidate
            break

    product_type = "device"
    type_patterns = [
        ("camera", r"\b(kamera|camera|eos|lumix)\b"),
        ("monitor", r"\b(monitor|display)\b"),
        ("watch", r"\b(watch|uhr)\b"),
        ("laptop", r"\b(laptop|notebook|thinkpad|ideapad|vivobook|zenbook)\b"),
        ("tablet", r"\btablet\b"),
        ("phone", r"\b(smartphone|handy)\b"),
        ("headphones", r"\b(kopfhorer|headphones|headset|earbuds)\b"),
        ("controller", r"\b(controller|gamepad)\b"),
    ]
    source = f"{text} {canonical_text(category)}"
    for candidate_type, pattern in type_patterns:
        if re.search(pattern, source):
            product_type = candidate_type
            break

    tokens = re.findall(r"\b[a-z]{1,12}[- ]?\d[a-z0-9-]{0,15}\b|\b[a-z]{2,8}\d{2,6}[a-z0-9-]*\b", text)
    cleaned: list[str] = []
    for token in tokens:
        token = re.sub(r"\s+", "", token)
        if re.fullmatch(r"\d+(?:gb|tb)", token):
            continue
        if token not in cleaned:
            cleaned.append(token)
    if brand and cleaned:
        model = cleaned[0].upper()
        return _compose(brand=brand, product_type=product_type, model=model, confidence=58)
    if brand:
        return _compose(brand=brand, product_type=product_type, model=BRAND_DISPLAY.get(brand, brand), confidence=38)
    return ProductIdentity(confidence=0)


def recognize_product(title: str, category: str = "") -> ProductIdentity:
    """Recognize a product from an ad title without network/AI calls.

    v3.0 deliberately starts with deterministic rules for the high-volume
    electronics families. Unknown/generic titles remain unclassified instead of
    being forced into a wrong market-price group.
    """
    text = canonical_text(title)
    if not text:
        return ProductIdentity()

    # Order matters: named ecosystems first, generic CPU/GPU and then fallback.
    for recognizer in (_apple, _playstation, _xbox, _nintendo, _steam_meta, _samsung_google, _gpu_cpu):
        identity = recognizer(text)
        if identity is not None and identity.key:
            return identity
    return _generic(text, category)


def identity_summary(identity: ProductIdentity) -> str:
    if not identity.key:
        return "Не распознано"
    return identity.label or identity.model or identity.key
