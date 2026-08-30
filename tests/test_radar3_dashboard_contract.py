from pathlib import Path

BOT = Path(__file__).resolve().parents[1].joinpath('bot.py').read_text(encoding='utf-8')


def test_radar3_dashboard_has_core_measurements():
    for phrase in (
        'Ждут первого повторного замера',
        'Готовы к замеру сейчас',
        'Candidate 15–29/ч, без Score',
        'Score Gate ≥30/ч',
        'Score подтверждён ≥2 раза',
        'Суммарный DT-observed прирост',
        'Early:', 'Strong:', 'Hot:',
        'Категории с живым спросом',
    ):
        assert phrase in BOT


def test_dashboard_groups_live_growth_by_category():
    block = BOT.split('async def _radar3_dashboard_snapshot', 1)[1].split('async def _radar3_dashboard_safe_snapshot', 1)[0]
    assert 'RadarObservation.category_key' in block
    assert 'func.sum(RadarObservation.total_delta)' in block
    assert '.group_by(RadarObservation.category_key)' in block
    assert 'RadarProduct.category_key' in block
    assert 'RadarProduct.demand_status' in block
