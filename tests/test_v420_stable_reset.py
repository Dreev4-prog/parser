from pathlib import Path


def test_version_and_single_service_defaults():
    root = Path(__file__).resolve().parents[1]
    assert (root / "VERSION").read_text().strip() == "4.2.0"
    distributed = (root / "distributed.py").read_text()
    assert 'STABLE_SINGLE_SERVICE_MODE = _env_bool("STABLE_SINGLE_SERVICE_MODE", True)' in distributed
    assert 'and not STABLE_SINGLE_SERVICE_MODE' in distributed


def test_false_robot_challenge_removed():
    root = Path(__file__).resolve().parents[1]
    parser = (root / "parser.py").read_text()
    assert '"access denied", "robot",' not in parser
    assert '"bist du ein roboter"' in parser
    assert 'reset_scan_browser_context' in parser


def test_stable_reset_is_one_lane_and_no_whole_category_recovery():
    root = Path(__file__).resolve().parents[1]
    bot = (root / "bot.py").read_text()
    assert 'APP_VERSION = "4.2.0"' in bot
    assert 'MAX_CONCURRENT_JOBS = 1' in bot
    assert 'SCAN_AUTO_RECOVERY_PASSES = 0' in bot
    assert 'SCAN_CATEGORY_ATTEMPTS = 1' in bot
    assert 'Stable Reset recovered page after browser recycle' in bot
    assert 'invalid_reason=%s' in bot


def test_robot_product_words_do_not_trigger_challenge():
    from parser import category_page_info_from_html

    info = category_page_info_from_html(
        "<html><body>Mähroboter Saugroboter Roboter</body></html>",
        requested_page=1,
        final_url="https://www.kleinanzeigen.de/s-test/seite:1/c0",
    )
    assert info.suspicious is False

    challenge = category_page_info_from_html(
        "<html><body>Are you a robot? captcha access denied</body></html>",
        requested_page=1,
        final_url="https://www.kleinanzeigen.de/s-test/seite:1/c0",
    )
    assert challenge.suspicious is True
