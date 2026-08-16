from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_v350_version_and_redis_dependency():
    assert (ROOT / "VERSION").read_text().strip() == "4.0.3"
    assert 'APP_VERSION = "4.0.3"' in (ROOT / "bot.py").read_text()
    assert "redis>=" in (ROOT / "requirements.txt").read_text()


def test_parser_worker_is_separate_from_telegram_polling():
    source = (ROOT / "parser_worker.py").read_text()
    assert "distributed_scan_worker" in source
    assert "recover_distributed_unfinished_scans" in source
    assert "start_polling" not in source


def test_views_worker_is_separate_background_lane():
    source = (ROOT / "views_worker.py").read_text()
    assert "observation_scheduler" in source
    assert "start_polling" not in source


def test_redis_stream_has_crash_recovery_and_ack_cleanup():
    source = (ROOT / "distributed.py").read_text()
    assert ".xreadgroup(" in source
    assert ".xautoclaim(" in source
    assert ".xack(" in source
    assert ".xdel(" in source
    assert "acquire_job_lock" in source


def test_cross_replica_category_and_traffic_coordination_present():
    source = (ROOT / "distributed.py").read_text()
    assert "category:lock" in source
    assert "category:progress" in source
    assert "category:result" in source
    assert "traffic:active:global" in source
    assert "report_traffic_refusal" in source


def test_scan_result_payload_is_small_primitive_dataclass():
    source = (ROOT / "bot.py").read_text()
    start = source.index("class ScanResult:")
    end = source.index("\n\ndef _calculate_scan_quality", start)
    block = source[start:end]
    # Redis serialization relies on ScanResult containing only primitive/list fields.
    assert "matched_ids: list[str] | None" in block
    assert "quality_score: int" in block
    assert "Listing" not in block
    assert "datetime" not in block


def test_distributed_db_pool_defaults_are_smaller_per_replica():
    source = (ROOT / "db.py").read_text()
    assert 'default_pool = "3" if distributed_mode else "5"' in source
    assert 'default_overflow = "2" if distributed_mode else "5"' in source
