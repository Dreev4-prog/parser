from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text()


def test_ai_lab_badge_does_not_query_legacy_ai_tables():
    block = BOT.split("async def _ai_unread_signal_count", 1)[1].split("async def _mark_ai_signals_seen", 1)[0]
    assert "return 0" in block
    assert "AIEarlyWinnerEvent" not in block
    assert "SessionLocal" not in block


def test_ai_lab_keyboard_is_radar3_only():
    block = BOT.split("def admin_ai_keyboard", 1)[1].split("def _ai_stage_label", 1)[0]
    assert "Обновить Radar 3.0" in block
    assert "adminradarauto" in block
    for legacy in ("adminai:new", "adminai:hidden", "adminai:momentum", "adminai:accuracy"):
        assert legacy not in block


def test_stale_adminai_sections_redirect_without_legacy_queries():
    block = BOT.split('async def admin_ai_section_handler', 1)[1].split('@dp.callback_query(F.data.startswith("aic:"))', 1)[0]
    assert "_admin_ai_dashboard_text" in block
    assert "_ai_candidate_rows" not in block
    assert "_admin_ai_accuracy_text" not in block


def test_stale_candidate_buttons_redirect_to_radar3():
    block = BOT.split('async def admin_ai_candidate_handler', 1)[1].split('@dp.callback_query(F.data == "adminviews")', 1)[0]
    assert "_admin_ai_dashboard_text" in block
    assert "_admin_ai_candidate(" not in block
