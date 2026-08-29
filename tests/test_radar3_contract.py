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
        self.assertIn('family_persistent >= 2', src)
        self.assertIn('consecutive_positive', src)
        self.assertIn('Initial counter is baseline-only and contributed 0 points', src)

    def test_clean_break_preserves_raw_listing_history_by_contract(self):
        start = RADAR.index('async def prepare_radar_v3_once(')
        src = RADAR[start:]
        self.assertIn('delete(RadarSnapshot)', src)
        self.assertIn('delete(RadarProduct)', src)
        self.assertNotIn('delete(Listing)', src)

    def test_scheduler_remeasures_exact_views_after_baseline(self):
        self.assertIn('radar_v3_observation_scheduler', BOT)
        self.assertIn('refresh_view_counts(rows, None, force=True, max_age_seconds=0, traffic_priority="background")', BOT)
        self.assertIn('radar_v3_record_refreshed', BOT)


if __name__ == '__main__':
    unittest.main()
