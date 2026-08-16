from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PARSER = (ROOT / "parser.py").read_text(encoding="utf-8")
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
WORKER = (ROOT / "hybrid_worker.py").read_text(encoding="utf-8")
ENV = (ROOT / ".env.example").read_text(encoding="utf-8")


def test_v370_version_and_hybrid_worker_profile():
    assert (ROOT / "VERSION").read_text().strip() == "4.1.2"
    assert 'APP_VERSION = "4.1.2"' in BOT
    assert 'SCAN_TRANSPORT", "hybrid"' in WORKER
    assert 'PARSER_WORKER_CONCURRENCY", "1"' in WORKER
    assert 'os.environ["SHARE_ACTIVE_CATEGORY_SCANS"] = "1"' in WORKER
    assert 'DIST_TRAFFIC_SHARED_COOLDOWN", "0"' in WORKER


def test_hybrid_uses_browser_storage_state_then_standalone_http_context():
    assert 'SCAN_TRANSPORT not in {"http", "browser", "hybrid"}' in PARSER
    assert 'self._browser_context.storage_state()' in PARSER
    assert 'self._playwright.request.new_context(' in PARSER
    assert 'storage_state=self._hybrid_storage_state' in PARSER
    assert 'await self._close_browser_runtime()' in PARSER


def test_hybrid_bulk_requests_do_not_render_browser_pages():
    assert 'self._hybrid_request_context.get(' in PARSER
    assert 'HYBRID_HTTP_TIMEOUT_MS' in PARSER
    assert 'HYBRID_SESSION_TTL_SECONDS' in PARSER
    assert 'await response.dispose()' in PARSER
    assert 'traffic_kind == "scan" and self.scan_transport == "hybrid"' in PARSER


def test_explicit_refusals_are_not_bypassed_by_browser_fallback():
    refusal = PARSER.index('if status in {403, 429}:', PARSER.index('async def _fetch_scan_hybrid_document'))
    raise_refusal = PARSER.index('raise TemporaryAccessError(status, url)', refusal)
    fallback = PARSER.index('self._hybrid_browser_fallbacks += 1', raise_refusal)
    assert refusal < raise_refusal < fallback
    assert 'Do not switch transports to defeat an explicit refusal.' in PARSER


def test_hybrid_reuses_lightweight_context_for_view_counter_path():
    assert 'self.scan_transport == "hybrid" and self._hybrid_request_context is not None' in PARSER
    assert 'source_prefix = "direct-hybrid"' in PARSER


def test_hybrid_progress_is_visible_and_env_documented():
    assert 'HTTP-first' in BOT
    assert 'HYBRID_CLOSE_BROWSER_AFTER_SEED=1' in ENV
    cfg = (ROOT / "railway.hybrid-worker.json").read_text(encoding="utf-8")
    assert 'python hybrid_worker.py' in cfg
