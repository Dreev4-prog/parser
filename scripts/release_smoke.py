#!/usr/bin/env python3
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def check(condition: bool, message: str) -> None:
    if not condition:
        raise SystemExit(f"FAIL: {message}")
    print(f"OK: {message}")


def main() -> int:
    check((ROOT / "VERSION").read_text().strip() == "4.23.10", "VERSION=4.23.10")
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    check(True, "all Python files parse recursively")

    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    radar = (ROOT / "radar.py").read_text(encoding="utf-8")
    velocity = (ROOT / "organic_velocity.py").read_text(encoding="utf-8")
    score = (ROOT / "early_winner.py").read_text(encoding="utf-8")

    check('RADAR_AUTOSCAN_DEPTH = 20' in bot, "Radar AutoScan depth=20")
    home_block = bot.split('def main_keyboard(', 1)[1].split('def post_scan_keyboard(', 1)[0]
    check('[InlineKeyboardButton(text="▶️ НОВЫЙ СКАН", callback_data="start_scan")]' in home_block and '[InlineKeyboardButton(text="📡 DT RADAR 3.0", callback_data="radar_home")]' in home_block,
          "Scan and Radar are equal full-width home actions")
    home_text_block = bot.split('def home_text(', 1)[1].split('async def _send_home_message(', 1)[0]
    check('DT PARSER — MARKET ANALYTICS' in home_text_block and '<b>Перед новым сканом:</b>' not in home_text_block,
          "home caption is Radar-equal Market Analytics UI")
    check('RADAR_CONTEXT_ENABLED = False' in bot and 'RADAR_CONTEXT_DEPTH = 0' in bot, "yesterday Context retired")
    scheduler = bot.split('async def radar_autoscan_scheduler', 1)[1].split('async def send_smart_export', 1)[0]
    check('_radar_autoscan_new_context_round' not in scheduler, "scheduler cannot launch yesterday Context")
    check('record_user_scan_radar3_baselines' in bot and 'record_user_scan_radar3_baselines' in radar, "today user scans feed Radar 3.0 baselines")
    check('class RadarObservation(Base):' in (ROOT / 'models.py').read_text(encoding='utf-8'),
          "Radar 3.0 observation table present")
    check('baseline_views=raw' in radar and 'admitted=0, saved=0' in radar,
          "first exact counter is baseline-only and cannot publish")
    check('RADAR_V3_FIRST_CHECK_MINUTES = 60' in radar and 'radar_v3_observation_scheduler' in bot,
          "first DT-owned remeasurement scheduled after 60 minutes")
    check('RADAR_V3_MAX_OBSERVATION_HOURS = 6' in radar and 'RADAR_V3_LIVE_RETENTION_HOURS = 24' in radar,
          "Radar evidence window stays 6h while live catalogue retention is 24h")
    check('RADAR_V3_NOISE_FLOOR_VPH = 3.0' in radar and 'RADAR_V3_CANDIDATE_PERCENTILE = 0.90' in radar and 'RADAR_V3_EARLY_PERCENTILE = 0.95' in radar and 'RADAR_V3_STRONG_PERCENTILE = 0.98' in radar,
          "Radar 3.2 uses category-adaptive P90/P95/P98 with 3/h noise floor")
    refresh_block = radar.split('async def radar_v3_record_refreshed', 1)[1].split('async def radar_v3_expire_observations', 1)[0]
    check('PASS 1 — persist raw DT measurements only' in refresh_block and 'PASS 2 — load each category cohort once' in refresh_block and 'thresholds_by_category' in refresh_block,
          "Radar 3.2 category evaluation is two-pass and order-stable inside each batch")
    check('RADAR_V3_EXCLUDED_GROUPS' in radar and all(f'"{g}"' in radar.split('RADAR_V3_EXCLUDED_GROUPS',1)[1].split('})',1)[0] for g in ('auto','immobilien','jobs','services','kurse','hilfe')),
          "non-product groups are excluded by canonical Radar scope")
    user_seed = radar.split('async def record_user_scan_radar3_baselines',1)[1].split('async def radar_v3_due_external_ids',1)[0]
    check('radar_v3_category_allowed(str(listing.category_key or ""))' in user_seed and 'radar_v3_category_allowed' in bot.split('def _radar_autoscan_category_allowed',1)[1].split('def _radar_autoscan_categories',1)[0],
          "AutoScan and user-scan baselines share the same Radar category policy")
    check('RADAR_AUTOSCAN_POLICY_VERSION = 7' in bot and 'state = _radar_autoscan_default_state()' in bot and 'raw_state = {}' in bot,
          "policy upgrade discards old AutoScan telemetry/progress")
    check('if str(obs.status) not in {"observed", "confirmed"}:' in radar and 'continue' in radar,
          "only category-qualified Early/Strong observations can publish DT Score")
    check('Initial counter is baseline-only and contributed 0 points' in radar,
          "initial counter contributes zero score")
    reset_block = radar.split('async def prepare_radar_v3_once() -> bool:', 1)[1].split('async def record_autoscan_hot(', 1)[0]
    check('delete(RadarSnapshot)' not in reset_block and 'delete(RadarProduct)' not in reset_block and 'delete(RadarObservation)' not in reset_block,
          "Radar startup guard is non-destructive")
    check('Radar 3.2 maintenance: preserve evidence; no destructive startup reset/backfill.' in bot,
          "legacy historical backfill disabled and maintenance is non-destructive")
    expire_block = radar.split('async def radar_v3_expire_stale_products', 1)[1].split('async def repair_radar_v3_historical_scores_once', 1)[0]
    check('current_score=0' not in expire_block and 'else_=RadarProduct.last_signal_score' in expire_block,
          "24h live expiry preserves confirmed Score between AutoScan passes")
    rollover_block = radar.split('async def radar_v3_rollover_successful_category', 1)[1].split('async def repair_radar_v3_historical_scores_once', 1)[0]
    check('RadarProduct.product_key.notin_' in rollover_block and 'status="historical"' in rollover_block,
          "successful category pass retires live families absent from the fresh verified set")
    autoscan_runner = bot.split('async def _run_radar_autoscan_round_inner', 1)[1].split('async def _run_radar_autoscan_round', 1)[0]
    check('radar_v3_rollover_successful_category' in autoscan_runner and 'result.matched_ids or []' in autoscan_runner,
          "category freshness rollover is wired into successful AutoScan completion")
    check('RADAR_V3_HISTORY_SCORE_REPAIR_SETTING' in radar and 'repair_radar_v3_historical_scores_once()' in bot,
          "pre-4.21.14 zeroed historical scores are repaired once")
    live_restore = radar.split('async def repair_radar_v3_live_retention_once', 1)[1].split('async def prepare_radar_v3_once', 1)[0]
    check('RADAR_V3_LIVE_RETENTION_REPAIR_SETTING' in radar and 'RadarProduct.last_signal_at >= live_cutoff' in live_restore and 'repair_radar_v3_live_retention_once()' in bot,
          "first 4.21.16 startup restores recent products expired by the old 6h live TTL")
    check('conditions.append(RadarProduct.status != "historical")' in radar and 'История · сигнал устарел' in bot,
          "live catalogue is separated from preserved Radar history")
    refresh_score_block = radar.split('async def refresh_radar_scores', 1)[1].split('async def radar_stats', 1)[0]
    check('str(product.latest_source or "") == "radar3_observed"' in refresh_score_block and 'signal_age_hours > RADAR_V3_LIVE_RETENTION_HOURS' in refresh_score_block,
          "legacy score refresh cannot resurrect stale Radar 3.2 History")
    check('Radar 3.0: legacy admission path disabled' in radar,
          "legacy scan/AI/verified-velocity publishers disabled")
    check('RadarAutoScanStopped' in bot and '_radar_autoscan_stop_event' in bot,
          "Hard Stop preserved")
    check('RADAR_AUTOSCAN_CATEGORY_TIMEOUT_SECONDS' in bot, "AutoScan watchdog preserved")
    traffic = (ROOT / 'traffic.py').read_text(encoding='utf-8')
    check('priority in {"background", "radar_checkpoint"}' in traffic and 'self._background_pauses > 0 and not is_radar_checkpoint' in traffic, "background pause preserved with dedicated Radar checkpoint exception")
    check('else "normal"' in radar and 'background_during_scans' not in radar[radar.find('detail_priority = ('):radar.find('detail_lane =', radar.find('detail_priority = ('))], "foreground Radar detail gate never self-classifies as background")
    check('traffic_priority="radar_checkpoint"' in bot and 'radar_v3_record_refreshed' in bot, "Radar 3.0 remeasurement uses throttled checkpoint lane")
    models = (ROOT / 'models.py').read_text(encoding='utf-8')
    launcher = (ROOT / 'service_launcher.py').read_text(encoding='utf-8')
    check('"ai-worker": "retired_ai_worker.py"' in launcher, "legacy AI service is inert")
    check('.with_for_update(skip_locked=True)' in radar and 'radar_v3_claim_due_external_ids' in radar, "cross-replica Radar claims use SKIP LOCKED")
    check('lease_owner' in models and 'lease_until' in models, "Radar observation lease fields present")
    check('radar_v3_expire_observations' in radar and 'RadarObservation.expires_at > now' in radar, "Radar observation TTL enforced before claims")
    check('pg_advisory_xact_lock(hashtext(:key))' in radar, "Radar preservation guard serialized across Parser replicas")
    check('autoscan_view_priority = "autoscan_idle" if idle_turbo else "scan_inline"' in bot and 'local_fallback_on_partial=False' in bot,
          "AutoScan exact views use audited idle-burst priority with bounded partial recovery")
    check('def admin_radar_autoscan_loading_keyboard()' in bot and '▶️ Запустить AutoScan' in bot and '⏹ Остановить' in bot, "AutoScan controls visible on Radar loading screen")
    check('asyncio.shield(task)' in bot and 'dashboard snapshot timed out; UI continues with controls' in bot, "Radar dashboard timeout cannot trap UI on loading screen")
    page = (ROOT / 'page_manager.py').read_text(encoding='utf-8')
    stable = (ROOT / 'stable_engine.py').read_text(encoding='utf-8')
    page_worker = (ROOT / 'page_worker.py').read_text(encoding='utf-8')
    check('v4200-core2-audit3' in page and 'v4200-core2-audit3' in stable and 'PAGE_CACHE_SCHEMA' in page_worker, "promotion parser cache schema aligned manager/worker/stable")
    date_manager = (ROOT / 'date_manager.py').read_text(encoding='utf-8')
    date_worker = (ROOT / 'date_worker.py').read_text(encoding='utf-8')
    check('v4200-core2-audit3' in date_manager and 'DATE_CACHE_SCHEMA' in date_worker, "date probe cache schema aligned manager/worker")
    view_manager = (ROOT / 'view_manager.py').read_text(encoding='utf-8')
    db_source = (ROOT / 'db.py').read_text(encoding='utf-8')
    parser_source = (ROOT / 'parser.py').read_text(encoding='utf-8')
    check('v4200-core2-audit3' in page and 'v4200-core2-audit3' in date_manager and 'v4200-core2-audit3' in view_manager, "Redis runtime namespaces isolated for v4.20 audited deploy")
    check('pg_advisory_xact_lock' in db_source, "PostgreSQL startup migrations serialized across services")
    check('_listing_identity_matches(final_url, ad_id)' in parser_source and '_view_endpoint_matches_ad_id(response.url, ad_id)' in parser_source, "exact views bound to listing/adId identity")
    check('if len(nums) != 1:' in parser_source, "ambiguous extra-info integers cannot masquerade as view counters")
    view_worker = (ROOT / 'view_counter_worker.py').read_text(encoding='utf-8')
    check('_fail_stream_message_for_local_fallback' in view_worker and 'remote_failed' in view_worker, "corrupt View Worker jobs fail fast to local fallback")
    check('_listing_identity_matches(final_url, external_id)' in parser_source and 'return None' in parser_source, "Lifecycle wrong-identity redirects remain UNKNOWN")
    check('canonical_id != external_id' in page and '_allowed_url(url)' in page, "Page Worker payload revalidates listing identity")
    check('item.page == expected_page' in date_manager, "Date Worker hint is bound to requested page")
    check('_category_feed_identity_matches(requested_url, final_url)' in parser_source, "category redirects preserve requested feed identity")
    check('for item in targets:' in bot and 'vr = results.get(url)' in bot, "partial exact-view maps cannot preserve stale counters")
    check(not list(ROOT.glob('DEPLOY_V4_*.md')), "historical deploy files removed from root")
    check((ROOT / "vinted_probe.py").is_file() and (ROOT / "vinted_probe_worker.py").is_file(), "isolated Vinted Probe worker present")
    check('"vinted-probe": "vinted_probe_worker.py"' in launcher, "service launcher routes Vinted Probe separately")
    vinted = (ROOT / "vinted_probe.py").read_text(encoding="utf-8")
    check("UNKNOWN is never converted to zero" in vinted and "wrong_identity" in vinted, "Vinted exact-metric path is fail-closed")
    check("does not solve anti-bot challenges" in vinted, "Vinted Probe has no challenge bypass logic")
    check("/api/v2/items/{item.item_id}/details" in vinted, "Vinted Probe tests current browser item-details endpoint")
    check("recovery_pages_used" in vinted and "recovery_complete" in vinted, "Vinted live pagination restores requested unique depth with bounded recovery")
    check("pagination.time` is telemetry/cache-buster data" in vinted, "Vinted pagination.time is not trusted as a snapshot cursor")
    lab = (ROOT / "vinted_lab.py").read_text(encoding="utf-8")
    check('"vinted-scan-worker": "vinted_scan_worker.py"' in launcher and '"vinted-metrics-worker": "vinted_metrics_worker.py"' in launcher, "Vinted Scan/Metrics workers are independently routed")
    check('callback_data="av:home"' in bot and '_vinted_watch_scan' in bot, "admin-only Vinted Lab has live progress UI")
    check('dtparser:vintedlab' in lab and 'SCAN_STREAM' in lab and 'METRICS_STREAM' in lab, "Vinted Lab uses an isolated Redis namespace")
    check('row.metric_status = "exact" if row.identity_ok and row.view_count is not None else "unknown"' in lab, "Vinted exact metrics remain fail-closed")
    vinted_radar = (ROOT / "vinted_radar.py").read_text(encoding="utf-8")
    check('VINTED_RADAR_SCOPE = "balanced_market_segments_v1"' in vinted_radar and 'VINTED_RADAR_TARGET_SEGMENTS = 120' in vinted_radar and 'VINTED_RADAR_MAX_SEGMENTS = 150' in vinted_radar, "Vinted Radar uses bounded balanced market segments")
    check('def balanced_catalog_segments_from_tree(' in lab and 'frontier[idx:idx + 1] = list(chosen.get("children") or [])' in lab, "Vinted market partition preserves non-overlapping tree coverage")
    check('item_catalog_id = _int(getattr(item, "catalog_id", None), 0) or int(catalog_id)' in lab, "Vinted Radar preserves precise item catalog id when available")
    check('VINTED_RADAR_MIN_PRICE_EUR = max(0.0' in vinted_radar and 'VintedScanItem.price_amount >= VINTED_RADAR_MIN_PRICE_EUR' in vinted_radar,
          "Vinted Radar enforces the 40 EUR baseline floor before scoring")
    check('VintedScanItem.created_at,' in vinted_radar and '.order_by(VintedScanItem.created_at.asc(), VintedScanItem.id.asc())' in vinted_radar,
          "Vinted Like Momentum uses real item observation timestamps")
    check('Only current Live items belong in current peer percentiles' in vinted_radar and 'for item_id in live_ids:' in vinted_radar,
          "Vinted current peer percentiles exclude expired learning rows")
    check('deal_interest = movement or' in vinted_radar and 'price_peer_count >= VINTED_RADAR_MIN_PRICE_PEERS' in vinted_radar,
          "Vinted Deals require a real price cohort plus demand evidence")
    check('Воронка наблюдений' in bot and 'snapshot.positive_movement' in bot,
          "Vinted Radar UI exposes repeat-observation demand coverage")
    check('class VintedRadarWatch(Base):' in models and '__tablename__ = "vinted_radar_watches"' in models,
          "Vinted Radar durable follow-up watch table present")
    check('VINTED_RADAR_FOLLOWUP_OFFSETS_MINUTES = (30, 60, 120, 180)' in lab and 'enqueue_radar_followup' in lab,
          "Vinted follow-up checkpoints use the isolated Metrics fleet")
    check('maintain_followup_lane as maintain_vinted_radar_followup_lane' in bot and 'await maintain_vinted_radar_followup_lane()' in bot,
          "Vinted follow-up maintenance is wired into the persistent Radar scheduler")
    check('VintedMetricHistory.source.like("radar_followup%")' in vinted_radar and '_coalesce_like_samples' in vinted_radar,
          "identity-bound follow-up likes feed Like Momentum without near-duplicate intervals")
    metrics_worker = (ROOT / 'vinted_metrics_worker.py').read_text(encoding='utf-8')
    check('purpose") or "") == "radar_followup"' in metrics_worker and 'save_radar_followup_sample' in metrics_worker,
          "Vinted Metrics Worker executes targeted Radar follow-ups")
    view_manager = (ROOT / 'view_manager.py').read_text(encoding='utf-8')
    check('deadline_seconds: float | None = None' in view_manager and 'preserving completed shards' in view_manager,
          "remote view deadlines preserve completed shards")
    check('self.autoscan_idle_view_limit' in traffic and 'is_autoscan_idle' in traffic and 'idle_burst_ok' in traffic,
          "Idle Turbo has a real traffic-manager burst lane")
    check('SCAN_CATEGORY_ATTEMPTS = 2' in bot and 'SCAN_AUTO_RECOVERY_PASSES = 1' in bot,
          "stable user scans get one in-launch clean recovery before partial UI")
    user_recovery = bot.split('async def auto_recover_partial_category', 1)[1].split('async def process_scan_job', 1)[0]
    check('reset_scan_browser_context()' in user_recovery and 'parser.prepare_category_scan()' in user_recovery and 'force_refresh=True' in user_recovery,
          "user partial recovery uses fresh context plus checkpoint-aware forced repair")
    check('callback_data=f"scanrecheck:{job.scan_id}"' in bot and 'Повторить только проблемные' in bot,
          "manual fallback retries only incomplete categories")
    check('async def _radar_autoscan_inline_recover_category' in bot and '{"partial", "radar_views"}' in bot,
          "Radar retries page/view partials inside the same round before review")
    check('review_transport' in bot and 'защитный режим Kleinanzeigen' in bot,
          "Radar exposes site-pressure review causes instead of hiding them under other")
    check('RADAR_AUTOSCAN_IDLE_PREFETCH_PAGES = _radar_env_int("RADAR_AUTOSCAN_IDLE_PREFETCH_PAGES", 16' in bot,
          "idle page prefetch is capped below the full 20-page category burst")

    print("\nDT Parser 4.23.10 Vinted Radar Follow-up Lane release smoke: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
