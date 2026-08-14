from datetime import datetime

from filters import apply_listing_settings
from models import Listing, UserSettings


def row(external_id: str, title: str, price: int) -> Listing:
    return Listing(
        external_id=external_id,
        category_key="pc",
        category="PC-Zubehör & Software",
        title=title,
        price_text=f"{price} €",
        price_eur=price,
        posted_text="10.08.2026",
        posted_date_msk="2026-08-10",
        url=f"https://example.test/{external_id}",
        first_seen_at=datetime(2026, 8, 10, 12, 0, 0),
        last_seen_at=datetime(2026, 8, 10, 12, 0, 0),
        is_active=True,
        is_promoted=False,
    )


def settings(**overrides) -> UserSettings:
    values = dict(
        user_id=1,
        output_mode="unique",
        smart_dedupe=True,
        clean_noise=True,
        period="today",
        price_filter="200_500",
        min_views=0,
        sort_mode="newest",
        include_words="",
        exclude_words="",
        page_limit=50,
    )
    values.update(overrides)
    return UserSettings(**values)


def test_exact_date_keeps_historical_rows_but_applies_price_and_unique_before_dedupe():
    rows = [
        row("1", "Sony PlayStation 5 Slim Disc 1TB", 450),
        row("2", "PS5 Slim Disc Konsole 1 TB", 440),
        row("3", "Apple TV 4K 128GB 3. Generation", 300),
        row("4", "High End Workstation", 4000),
    ]

    result = apply_listing_settings(
        rows,
        settings(),
        exact_date_scan=True,
        apply_output_mode=True,
    )

    # Historical 10.08 rows are not affected by the legacy period field.
    # 4000 EUR is removed by the user's price filter.
    # The two PS5 ads are the same product family, so neither is "unique".
    assert [r.external_id for r in result] == ["3"]


def test_non_unique_mode_still_uses_smart_dedupe():
    rows = [
        row("1", "Sony PlayStation 5 Slim Disc 1TB", 450),
        row("2", "Sony PlayStation 5 Slim Disc 1TB", 450),
    ]
    result = apply_listing_settings(
        rows,
        settings(output_mode="all", price_filter="any"),
        exact_date_scan=True,
        apply_output_mode=True,
    )
    assert len(result) == 1


def test_legacy_period_field_is_ignored_for_all_listing_settings():
    rows = [row("legacy", "Apple TV 4K 128GB 3. Generation", 300)]
    result = apply_listing_settings(
        rows,
        settings(output_mode="all", smart_dedupe=False, period="1h"),
        exact_date_scan=False,
        apply_output_mode=True,
    )
    assert [r.external_id for r in result] == ["legacy"]


def test_min_views_threshold_filters_result_after_view_collection():
    low = row("low", "Apple TV 4K 128GB", 300)
    low.view_count = 49
    high = row("high", "PlayStation Portal", 200)
    high.view_count = 50
    missing = row("missing", "Nintendo Switch OLED", 250)
    missing.view_count = None

    result = apply_listing_settings(
        [low, high, missing],
        settings(output_mode="all", smart_dedupe=False, price_filter="any", min_views=50),
        exact_date_scan=True,
        apply_output_mode=True,
    )

    assert [r.external_id for r in result] == ["high"]
