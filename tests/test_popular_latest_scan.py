from pathlib import Path


def test_popular_now_is_latest_successful_scan_only():
    source = (Path(__file__).parents[1] / "bot.py").read_text(encoding="utf-8")
    assert 'UserScan.status == "done"' in source
    category_block = source[source.index("async def get_category_scan_rows"):source.index("def _category_scan_dates")]
    assert "ScanListing.scan_id == scan.id" in category_block
    assert "ScanListing.scan_id.in_(" not in category_block

    growth_block = source[source.index("async def get_category_growth_rows"):source.index("def _scan_list_button")]
    assert "scan = await get_latest_scan_for_category" in growth_block
    assert "for scan in scans" not in growth_block
