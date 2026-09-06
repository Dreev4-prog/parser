from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADAR = (ROOT / "vinted_radar.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def test_radar_has_40_eur_hard_floor_and_sql_gate():
    assert 'VINTED_RADAR_MIN_PRICE_EUR", "40"' in RADAR
    assert "row_price < VINTED_RADAR_MIN_PRICE_EUR" in RADAR
    assert "VintedScanItem.price_amount >= VINTED_RADAR_MIN_PRICE_EUR" in RADAR
    assert "дешёвый мусор ниже порога не участвует" in BOT


def test_catalog_measurement_uses_item_persist_time_not_round_start():
    builder = RADAR.split("async def _build_snapshot", 1)[1]
    assert "VintedScanItem.created_at," in builder
    assert "VintedScanItem.created_at >= history_cutoff" in builder
    assert ".order_by(VintedScanItem.created_at.asc(), VintedScanItem.id.asc())" in builder
    assert "Using VintedScan.created_at made every item" in builder


def test_peer_percentiles_are_current_live_only():
    scoring = RADAR.split("def _score_snapshot_rows", 1)[1].split("async def _build_snapshot", 1)[0]
    assert "for item_id in live_ids:" in scoring
    assert "Only current Live items belong in current peer percentiles" in scoring
    assert "expired 7-day learning rows vote" in scoring
    assert '"bucket": _age_bucket(min(current_age, 23.999))' in scoring


def test_price_edge_requires_real_cohort_and_deal_requires_interest():
    assert 'VINTED_RADAR_MIN_PRICE_PEERS", "8"' in RADAR
    assert "price_peer_count >= VINTED_RADAR_MIN_PRICE_PEERS" in RADAR
    assert "deal_interest = movement or (likes is not None and likes >= 2 and like_percentile >= 0.50)" in RADAR
    assert "if strong_deal and deal_interest and score >= 40:" in RADAR
    assert "price_peer_count=price_peer_count" in RADAR


def test_unknown_catalog_does_not_become_one_mega_price_market():
    assert "Unknown catalog ids must not collapse into one artificial mega-market." in RADAR
    assert "if catalog > 0 and price is not None and price >= VINTED_RADAR_MIN_PRICE_EUR:" in RADAR


def test_admin_radar_exposes_observation_funnel():
    assert "Воронка наблюдений" in BOT
    assert "snapshot.single_observation" in BOT
    assert "snapshot.repeat_observation" in BOT
    assert "snapshot.positive_movement" in BOT
    assert "без сигнала" in BOT
