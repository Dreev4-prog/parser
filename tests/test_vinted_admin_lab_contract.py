from __future__ import annotations

import os
from pathlib import Path

from sqlalchemy import BigInteger

import service_launcher
from models import VintedScanItem

ROOT = Path(__file__).resolve().parents[1]


def test_vinted_scan_item_uses_bigint_identity():
    assert isinstance(VintedScanItem.__table__.c.item_id.type, BigInteger)


def test_vinted_category_tree_contract_is_nested_and_live():
    lab = (ROOT / "vinted_lab.py").read_text(encoding="utf-8")
    assert "/api/v2/catalog/initializers" in lab
    assert 'dtos.get("catalogs")' in lab
    assert "flatten_catalog_tree" in lab
    assert "parent_id" in lab


def test_service_launcher_routes_isolated_vinted_workers(monkeypatch):
    monkeypatch.delenv("DT_SERVICE_ROLE", raising=False)
    monkeypatch.setenv("RAILWAY_SERVICE_NAME", "Vinted Scan Worker")
    assert service_launcher._role() == "vinted-scan-worker"
    monkeypatch.setenv("RAILWAY_SERVICE_NAME", "Vinted Metrics Worker")
    assert service_launcher._role() == "vinted-metrics-worker"
    monkeypatch.setenv("RAILWAY_SERVICE_NAME", "Vinted Probe")
    assert service_launcher._role() == "vinted-probe"


def test_admin_vinted_lab_is_present_and_fail_closed():
    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    lab = (ROOT / "vinted_lab.py").read_text(encoding="utf-8")
    scan_worker = (ROOT / "vinted_scan_worker.py").read_text(encoding="utf-8")
    metric_worker = (ROOT / "vinted_metrics_worker.py").read_text(encoding="utf-8")
    assert 'text="🟣 Vinted Lab"' in bot
    assert 'callback_data="av:home"' in bot
    assert "_vinted_watch_scan" in bot
    assert "scan_percent" in lab and "metrics_percent" in lab
    assert "dtparser:vintedlab" in lab
    assert "Vinted Scan Worker requires REDIS_URL" in scan_worker
    assert "Vinted Metrics Worker requires REDIS_URL" in metric_worker
    assert 'row.metric_status = "exact" if row.identity_ok and row.view_count is not None else "unknown"' in lab


def test_vinted_session_json_is_one_optional_secret():
    probe = (ROOT / "vinted_probe.py").read_text(encoding="utf-8")
    assert 'VINTED_SESSION_JSON' in probe
    assert "session_cookie_names" in probe
