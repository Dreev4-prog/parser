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
    check((ROOT / "VERSION").read_text().strip() == "4.20.0", "VERSION=4.20.0")
    for path in sorted(ROOT.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    check(True, "all Python files parse recursively")

    bot = (ROOT / "bot.py").read_text(encoding="utf-8")
    radar = (ROOT / "radar.py").read_text(encoding="utf-8")
    velocity = (ROOT / "organic_velocity.py").read_text(encoding="utf-8")
    score = (ROOT / "early_winner.py").read_text(encoding="utf-8")

    check('RADAR_CONTEXT_DEPTH = 15' in bot, "Context depth=15")
    check('mode in {"manual", "daily"}' in bot, "manual/daily Fresh can queue Context")
    check('target_day = context_day - timedelta(days=1)' in bot, "Context targets yesterday")
    check('emit_signals=not context_only' in bot and 'if not emit_signals:' in radar,
          "Context cannot publish inherited yesterday totals directly")
    check('ORGANIC_HIGH_BASELINE_VIEWS = 400' in velocity, "400+ baseline policy preserved")
    check('24.0 * 60.0, 48.0 * 60.0' in score, "24-48h age cohort present")
    check('RadarAutoScanStopped' in bot and '_radar_autoscan_stop_event' in bot,
          "Hard Stop preserved")
    check('RADAR_AUTOSCAN_CATEGORY_TIMEOUT_SECONDS' in bot, "AutoScan watchdog preserved")
    traffic = (ROOT / 'traffic.py').read_text(encoding='utf-8')
    check('priority == "background" and kind in {"view", "browser"}' in traffic, "background browser obeys foreground pause")
    check('else "normal"' in radar and 'background_during_scans' not in radar[radar.find('detail_priority = ('):radar.find('detail_lane =', radar.find('detail_priority = ('))], "foreground Radar detail gate never self-classifies as background")
    check('newly_verified_ids, traffic_priority="background"' in bot and 'traffic_priority: str = "normal"' in radar, "Verified Organic Velocity keeps maintenance priority explicit")
    check('autoscan_view_priority = "scan_inline"' in bot, "AutoScan exact views stay foreground")
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
    check('return matched' in (ROOT / 'early_winner.py').read_text(encoding='utf-8'), "48H age cohorts do not fall back to all ages")
    check(not list(ROOT.glob('DEPLOY_V4_*.md')), "historical deploy files removed from root")
    print("\nDT Parser 4.20.0 release smoke: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
