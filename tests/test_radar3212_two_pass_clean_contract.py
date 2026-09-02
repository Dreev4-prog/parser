from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADAR = (ROOT / 'radar.py').read_text(encoding='utf-8')
BOT = (ROOT / 'bot.py').read_text(encoding='utf-8')


def _refresh_block():
    return RADAR.split('async def radar_v3_record_refreshed', 1)[1].split('async def radar_v3_expire_observations', 1)[0]


def test_two_pass_persists_before_building_category_cohorts():
    src = _refresh_block()
    assert 'PASS 1 — persist raw DT measurements only' in src
    assert 'await session.commit()' in src
    assert 'PASS 2 — load each category cohort once' in src
    assert src.index('PASS 1 — persist raw DT measurements only') < src.index('PASS 2 — load each category cohort once')
    assert 'cohort_by_category' in src
    assert 'thresholds_by_category' in src


def test_canonical_scope_blocks_non_product_groups_everywhere():
    for group in ('auto', 'immobilien', 'jobs', 'services', 'kurse', 'hilfe'):
        assert f'"{group}"' in RADAR.split('RADAR_V3_EXCLUDED_GROUPS', 1)[1].split('})', 1)[0]
    auto = RADAR.split('async def record_autoscan_hot_detailed', 1)[1].split('async def record_user_scan_radar3_baselines', 1)[0]
    user = RADAR.split('async def record_user_scan_radar3_baselines', 1)[1].split('async def radar_v3_due_external_ids', 1)[0]
    assert 'radar_v3_category_allowed(str(category_key))' in auto
    assert 'radar_v3_category_allowed(str(listing.category_key or ""))' in user
    helper = BOT.split('def _radar_autoscan_category_allowed', 1)[1].split('def _radar_autoscan_categories', 1)[0]
    assert 'radar_v3_category_allowed' in helper


def test_policy_upgrade_discards_old_telemetry_but_preserves_schedule_preferences():
    assert 'RADAR_AUTOSCAN_POLICY_VERSION = 6' in BOT
    block = BOT.split('stored_policy = max(0, int(raw_state.get("policy_version") or 0))', 1)[1].split('legacy_active = False', 1)[0]
    assert 'state = _radar_autoscan_default_state()' in block
    assert 'daily_enabled = bool(state.get("daily_enabled"))' in block
    assert 'daily_time = str(state.get("daily_time")' in block
    assert 'state["skip_daily_if_completed_today"] = skip_daily' in block
    assert 'raw_state = {}' in block


def test_release_has_fresh_reset_marker_and_no_old_absolute_gate_constants():
    assert 'dt_radar_v3_observed_demand_reset_v6_radar32_two_pass_clean' in RADAR
    assert 'RADAR_V3_CANDIDATE_VPH =' not in RADAR
    assert 'RADAR_V3_SCORE_VPH =' not in RADAR
    assert 'RADAR_V3_STRONG_VPH =' not in RADAR
