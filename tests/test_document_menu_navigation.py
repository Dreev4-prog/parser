from pathlib import Path


def _handler_block(source: str, marker: str, next_marker: str) -> str:
    start = source.index(marker)
    end = source.index(next_marker, start)
    return source[start:end]


def test_document_attached_main_menu_uses_safe_navigation():
    source = Path(__file__).resolve().parents[1].joinpath("bot.py").read_text(encoding="utf-8")
    pairs = [
        ("async def home(", "@dp.callback_query(F.data == \"post_settings\")"),
        ("async def settings(", "@dp.callback_query(F.data == \"mode_help\")"),
        ("async def my_scans(", "@dp.callback_query(F.data == \"archive_my_scans\")"),
        ("async def groups(", "@dp.callback_query(F.data.startswith(\"grp:\"))"),
    ]
    for marker, next_marker in pairs:
        block = _handler_block(source, marker, next_marker)
        assert "_edit_or_answer(" in block, marker


def test_navigation_clears_fsm_for_scans_and_categories():
    source = Path(__file__).resolve().parents[1].joinpath("bot.py").read_text(encoding="utf-8")
    my_scans = _handler_block(source, "async def my_scans(", "@dp.callback_query(F.data == \"archive_my_scans\")")
    groups = _handler_block(source, "async def groups(", "@dp.callback_query(F.data.startswith(\"grp:\"))")
    assert "state: FSMContext" in my_scans and "await state.clear()" in my_scans
    assert "state: FSMContext" in groups and "await state.clear()" in groups
