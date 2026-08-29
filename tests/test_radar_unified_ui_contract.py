from pathlib import Path

BOT = Path(__file__).resolve().parents[1] / "bot.py"
TEXT = BOT.read_text(encoding="utf-8")


def test_autoscan_ui_is_presented_as_one_unified_48h_round():
    assert 'Unified 48H Radar · сегодня + вчера' in TEXT
    assert 'Глубина круга: <b>15 сегодня + 15 вчера на категорию</b>' in TEXT
    assert '① Сегодня · 15 страниц · Fresh' in TEXT
    assert '② Вчера · 15 страниц · 24–48H Context' in TEXT
    assert 'Общий прогресс круга:' in TEXT


def test_fresh_completion_is_stage_not_round_completion():
    assert 'DT Radar — этап 1/2 · сегодня завершён' in TEXT
    assert 'Этап 1/2 готов. Тот же <b>Unified 48H круг</b> автоматически продолжится этапом 2/2' in TEXT


def test_context_completion_closes_unified_round():
    assert 'DT Radar — Unified 48H круг завершён' in TEXT
    assert 'DT Radar — Unified 48H · этап 2/2' in TEXT
    assert 'Это продолжение того же 48H круга' in TEXT


def test_old_misleading_circle_mode_labels_are_removed_from_live_panel():
    assert 'Режим круга: <b>{mode_label}</b>' not in TEXT
    assert 'Слой: <b>{\'сегодня · Fresh\'' not in TEXT
