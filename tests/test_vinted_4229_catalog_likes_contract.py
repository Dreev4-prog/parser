from pathlib import Path

from vinted_probe import normalize_catalog_item

ROOT = Path(__file__).resolve().parents[1]


def test_catalog_like_is_preserved_from_public_catalog():
    item = normalize_catalog_item({
        "id": 99123,
        "title": "Test",
        "url": "https://www.vinted.de/items/99123-test",
        "favourite_count": 17,
    })
    assert item is not None
    assert item.catalog_favourite_count == 17


def test_vinted_4229_ui_uses_catalog_likes_without_calling_them_exact():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    lab = (ROOT / "vinted_lab.py").read_text(encoding="utf-8")
    assert "Likes из каталога" in bot
    assert "catalog_favourite_count" in lab
    assert "catalog_like_delta" in lab
    assert '"catalog_likes": catalog_likes' in lab


def test_unknown_catalog_like_is_not_coerced_to_zero_in_item_card():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert 'fav = str(fav_value) if fav_value is not None else "UNKNOWN"' in bot
    assert 'fav_source = "detail" if detail_fav is not None else ("catalog" if catalog_fav is not None else "UNKNOWN")' in bot
