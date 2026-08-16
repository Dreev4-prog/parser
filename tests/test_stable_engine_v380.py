from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")
MODELS = (ROOT / "models.py").read_text(encoding="utf-8")
WORKER = (ROOT / "stable_worker.py").read_text(encoding="utf-8")
ENGINE = (ROOT / "stable_engine.py").read_text(encoding="utf-8")
ENV = (ROOT / ".env.example").read_text(encoding="utf-8")


def test_v380_version_and_stable_worker():
    assert (ROOT / "VERSION").read_text().strip() == "4.1.2"
    assert 'APP_VERSION = "4.1.2"' in BOT
    assert 'os.environ["STABLE_SCAN_ENGINE"] = "1"' in WORKER
    assert 'os.environ["SHARE_ACTIVE_CATEGORY_SCANS"] = "1"' in WORKER
    assert 'os.environ["PRIMARY_SCAN_INLINE_VIEWS"] = "0"' in WORKER
    assert 'python stable_worker.py' in (ROOT / "railway.stable-worker.json").read_text()


def test_persistent_checkpoint_schema_exists():
    assert 'class StablePageCheckpoint' in MODELS
    assert 'class StableDateIndex' in MODELS
    assert 'class StableCategoryJob' in MODELS
    assert 'uq_stable_page_checkpoint' in MODELS
    assert 'uq_stable_date_index' in MODELS


def test_stable_locator_is_sequential_not_jump_binary():
    marker = 'if STABLE_SCAN_ENGINE:'
    start = BOT.index(marker, BOT.index('async def locate_feed'))
    end = BOT.index('low_newer = 0', start)
    branch = BOT[start:end]
    assert 'while page <= effective_limit:' in branch
    assert 'page += 1' in branch
    assert 'stable_fetch(page, "stable_scan")' in branch
    assert 'load_date_index' in branch
    assert 'save_date_index' in branch


def test_verified_pages_use_postgres_checkpoint_before_network():
    fetch = BOT[BOT.index('async def fetch(page: int, phase: str)'):BOT.index('async def stable_fetch', BOT.index('async def fetch(page: int, phase: str)'))]
    assert 'load_page_checkpoint' in fetch
    assert 'save_page_checkpoint' in fetch
    assert 'postgres-checkpoint' in fetch
    assert 'record_page_failure' in fetch


def test_view_work_is_removed_from_default_foreground_scan():
    assert 'PRIMARY_SCAN_INLINE_VIEWS = os.getenv("PRIMARY_SCAN_INLINE_VIEWS", "0")' in BOT
    assert 'if inline_view_counts:' in BOT
    assert 'OBSERVATION_SCHEDULE_HOURS = (0, 3, 6, 12)' in BOT
    assert 'target_hours == 0 and membership.initial_view_count is None' in BOT


def test_checkpoint_payload_round_trip_contract_is_implemented():
    assert 'def _serialize_page_info' in ENGINE
    assert 'def _deserialize_page_info' in ENGINE
    assert '"external_id": item.external_id' in ENGINE
    assert 'ParsedListing(**item)' in ENGINE
    assert 'CategoryPageInfo(items=items, **payload)' in ENGINE


def test_stable_variables_documented():
    for name in (
        "STABLE_SCAN_ENGINE=1",
        "SHARE_ACTIVE_CATEGORY_SCANS=1",
        "STABLE_PAGE_RETRIES=3",
        "STABLE_PAGE_CHECKPOINT_TTL_SECONDS=300",
        "STABLE_DATE_INDEX_TTL_SECONDS=900",
        "PRIMARY_SCAN_INLINE_VIEWS=0",
    ):
        assert name in ENV
