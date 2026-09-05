import json
from pathlib import Path

import pytest

from vinted_local_session import sanitize_local_session

ROOT = Path(__file__).resolve().parents[1]


def _cookie(name: str, value: str = "x", domain: str = ".vinted.de") -> dict:
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": "/",
        "secure": True,
        "httpOnly": True,
    }


def test_local_helper_manifest_is_first_party_vinted_only():
    manifest = json.loads((ROOT / "vinted_local_helper" / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["manifest_version"] == 3
    hosts = set(manifest["host_permissions"])
    assert hosts == {"https://www.vinted.de/*", "https://vinted.de/*"}
    assert "cookies" in manifest["permissions"]
    assert "<all_urls>" not in hosts


def test_local_helper_does_not_persist_cookie_payload_in_extension_storage():
    bg = (ROOT / "vinted_local_helper" / "background.js").read_text(encoding="utf-8")
    assert 'chrome.cookies.getAll({ domain: "vinted.de" })' in bg
    assert 'chrome.storage.local.remove(STORAGE_KEY)' in bg
    assert "session = {" in bg
    assert "cookies: filtered" in bg
    assert "dtVintedCookies" not in bg


def test_local_session_requires_confirmed_user_identity_and_access_cookie():
    with pytest.raises(ValueError, match="login_not_confirmed"):
        sanitize_local_session({"cookies": [_cookie("access_token_web")]})
    with pytest.raises(ValueError, match="access_cookie_missing"):
        sanitize_local_session({"authenticated_user_id": "123", "cookies": [_cookie("foo")]})


def test_local_session_filters_non_vinted_domains():
    result = sanitize_local_session({
        "authenticated_user_id": "123",
        "cookies": [
            _cookie("access_token_web", "a"),
            _cookie("refresh_token_web", "b"),
            _cookie("evil", "bad", domain=".example.com"),
        ],
        "user_agent": "Chrome",
        "locale": "de-DE",
    })
    names = {x["name"] for x in result["cookies"]}
    assert "access_token_web" in names
    assert "refresh_token_web" in names
    assert "evil" not in names
    assert result["authenticated_user_id"] == "123"
    assert result["captured_by"] == "dt-vinted-local-helper"


def test_admin_copy_explicitly_uses_local_chrome_not_railway_browser():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    worker = (ROOT / "vinted_session_worker.py").read_text(encoding="utf-8")
    assert "Войти через мой Chrome" in bot
    assert "обычном Chrome" in bot
    assert "Railway больше не открывает страницу Vinted" in bot
    assert "https://www.vinted.de/member/general/login#dtv=" in worker
