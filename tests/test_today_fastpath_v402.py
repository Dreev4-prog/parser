from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text()


def test_v402_version():
    assert (ROOT / "VERSION").read_text().strip() == "4.0.3"
    assert 'APP_VERSION = "4.0.3"' in BOT


def test_today_skips_date_locator():
    assert 'today_fast_path = target_day == moscow_today' in BOT
    assert 'свежая дата обрабатывается последовательным проходом с первой страницы' in BOT
    assert 'candidate_page=1' in BOT


def test_partial_result_is_never_reused_as_final_cache():
    assert 'def _cacheable_category_result' in BOT
    assert 'result.date_complete' in BOT
    assert 'await COORDINATOR.delete_category_result(inflight_key)' in BOT


def test_cache_namespace_invalidates_old_v400_v401_results():
    assert 'return f"v402:{category_key}:date:{target_date}:depth:{depth}"' in BOT


def test_partial_zero_is_not_presented_as_verified_zero():
    assert 'Нулевой результат не считается окончательным' in BOT
