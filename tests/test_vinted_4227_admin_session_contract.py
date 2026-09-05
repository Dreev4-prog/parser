from pathlib import Path

import service_launcher

ROOT = Path(__file__).resolve().parents[1]


def test_service_launcher_routes_vinted_session_worker(monkeypatch):
    monkeypatch.delenv("DT_SERVICE_ROLE", raising=False)
    monkeypatch.setenv("RAILWAY_SERVICE_NAME", "Vinted Session Worker")
    assert service_launcher._role() == "vinted-session-worker"


def test_admin_panel_has_one_click_session_flow():
    source = (ROOT / "bot.py").read_text(encoding="utf-8")
    assert 'text="🔐 Vinted Session"' in source
    assert 'callback_data="av:sessionnew"' in source
    assert 'text="🌐 Открыть локальный вход"' in source
    assert "create_session_ticket" in source
    assert "get_session_service" in source


def test_session_worker_is_isolated_and_local_browser_only():
    source = (ROOT / "vinted_session_worker.py").read_text(encoding="utf-8")
    assert "Vinted Session Worker requires DATABASE_URL" in source
    assert "access_log=None" in source
    assert "/api/local/import" in source
    assert "/helper.zip" in source
    assert "admin-local-browser-helper" in source
    assert "playwright" not in source.lower()
    assert "keyboard.insert_text" not in source
    assert "save_vinted_session" in source
    local = (ROOT / "vinted_local_session.py").read_text(encoding="utf-8")
    assert "access_token_web" in local


def test_metrics_worker_hot_reloads_admin_db_session():
    source = (ROOT / "vinted_metrics_worker.py").read_text(encoding="utf-8")
    provider = (ROOT / "vinted_browser_metrics.py").read_text(encoding="utf-8")
    assert "load_vinted_session_json" in source
    assert "session_fingerprint" in source
    assert "provider.reload_session(raw)" in source
    assert '"session_source"] = "admin-db"' in source
    assert "async def reload_session" in provider


def test_session_store_persists_session_not_password_fields():
    source = (ROOT / "vinted_session_store.py").read_text(encoding="utf-8")
    assert 'SESSION_KEY = "vinted_session_json"' in source
    assert "save_vinted_session" in source
    assert "password" not in source.lower()
    assert "TICKET_PREFIX" in source
