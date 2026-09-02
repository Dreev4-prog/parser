from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
RADAR = (ROOT / 'radar.py').read_text(encoding='utf-8')
MODELS = (ROOT / 'models.py').read_text(encoding='utf-8')
BOT = (ROOT / 'bot.py').read_text(encoding='utf-8')


class Radar3ObservedDemandContractTests(unittest.TestCase):
    def test_first_counter_is_baseline_only(self):
        start = RADAR.index('async def record_autoscan_hot_detailed(')
        end = RADAR.index('async def record_autoscan_hot(', start)
        src = RADAR[start:end]
        self.assertIn('baseline_views=raw', src)
        self.assertIn('admitted=0', src)
        self.assertNotIn('classify_radar_signal(', src)

    def test_legacy_publishers_are_disabled(self):
        for name, ret in [('record_scan_hot', 'return 0'), ('record_verified_velocity_signals', 'return 0'), ('record_ai_candidate', 'return None')]:
            start = RADAR.index(f'async def {name}')
            next_def = RADAR.find('\nasync def ', start + 10)
            src = RADAR[start: next_def if next_def != -1 else len(RADAR)]
            self.assertIn(ret, src)

    def test_observation_model_has_dt_owned_clocks(self):
        start = MODELS.index('class RadarObservation(Base):')
        end = MODELS.index('class RadarLifecycleWatch(Base):', start)
        src = MODELS[start:end]
        for name in ('baseline_views', 'baseline_at', 'last_views', 'last_measured_at',
                     'checkpoint_count', 'consecutive_positive', 'total_delta',
                     'current_vph', 'next_check_at'):
            self.assertIn(name, src)

    def test_stage_rules_require_observed_evidence(self):
        start = RADAR.index('async def radar_v3_record_refreshed(')
        end = RADAR.index('async def radar_v3_expire_stale_products(', start)
        src = RADAR[start:end]
        self.assertIn('RADAR_V3_CANDIDATE_PERCENTILE', src)
        self.assertIn('RADAR_V3_EARLY_PERCENTILE', src)
        self.assertIn('RADAR_V3_STRONG_PERCENTILE', src)
        self.assertIn('pct >= RADAR_V3_EARLY_PERCENTILE', src)
        self.assertIn('pct >= RADAR_V3_STRONG_PERCENTILE', src)
        self.assertIn('Initial counter is baseline-only and contributed 0 points', src)

    def test_startup_guard_is_non_destructive(self):
        start = RADAR.index('async def prepare_radar_v3_once(')
        src = RADAR[start:RADAR.index('async def record_autoscan_hot(', start)]
        self.assertNotIn('delete(RadarSnapshot)', src)
        self.assertNotIn('delete(RadarProduct)', src)
        self.assertNotIn('delete(RadarObservation)', src)
        self.assertNotIn('delete(Listing)', src)
        self.assertIn('no Radar tables were deleted', src)

    def test_scheduler_remeasures_exact_views_after_baseline(self):
        self.assertIn('radar_v3_observation_scheduler', BOT)
        self.assertIn('refresh_view_counts(rows, None, force=True, max_age_seconds=0, traffic_priority="radar_checkpoint")', BOT)
        self.assertIn('radar_v3_record_refreshed', BOT)

    def test_legacy_ai_runtime_files_are_removed(self):
        self.assertFalse((ROOT / 'ai_worker.py').exists())
        self.assertFalse((ROOT / 'ai_manager.py').exists())
        self.assertTrue((ROOT / 'retired_ai_worker.py').exists())

    def test_cross_replica_claim_uses_skip_locked_and_lease(self):
        self.assertIn('radar_v3_claim_due_external_ids', RADAR)
        self.assertIn('.with_for_update(skip_locked=True)', RADAR)
        self.assertIn('RadarObservation.lease_until', RADAR)
        self.assertIn('row.lease_owner = owner', RADAR)
        self.assertIn('radar_v3_release_claims', RADAR)
        self.assertIn('radar_v3_claim_due_external_ids(owner, limit=250)', BOT)

    def test_admin_radar3_page_replaces_old_ai_lab(self):
        self.assertIn('DT Radar 3.2 · ADAPTIVE LIVE', BOT)
        admin = BOT.split('def admin_keyboard', 1)[1].split('def admin_back_keyboard', 1)[0]
        self.assertNotIn('DT AI Lab', admin)
        self.assertIn('DT Radar 3.0', admin)

    def test_checkpoint_lane_survives_autoscan_without_becoming_foreground(self):
        traffic = (ROOT / 'traffic.py').read_text(encoding='utf-8')
        self.assertIn('priority == "radar_checkpoint"', traffic)
        self.assertIn('not is_radar_checkpoint', traffic)
        self.assertIn('traffic_priority="radar_checkpoint"', BOT)
        self.assertIn('radar_v3_view_refresh_lock', BOT)

    def test_ttl_is_authoritative_and_expiry_is_independent(self):
        self.assertIn('RadarObservation.expires_at > now', RADAR)
        self.assertIn('async def radar_v3_expire_observations()', RADAR)
        self.assertIn('if obs.expires_at and measured_at > obs.expires_at:', RADAR)
        self.assertIn('expired_obs = await radar_v3_expire_observations()', BOT)
        self.assertIn('expired_products = await radar_v3_expire_stale_products()', BOT)

    def test_startup_reset_is_cross_replica_atomic(self):
        start = RADAR.index('async def prepare_radar_v3_once(')
        src = RADAR[start:RADAR.index('async def record_autoscan_hot(', start)]
        self.assertIn('pg_advisory_xact_lock', src)
        self.assertIn('RADAR_V3_RESET_SETTING', src)


if __name__ == '__main__':
    unittest.main()
