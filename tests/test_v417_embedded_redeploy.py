from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BOT = (ROOT / "bot.py").read_text(encoding="utf-8")


def _embedded_block():
    return BOT.split("async def _start_embedded_fleet_fallback", 1)[1].split("async def main()", 1)[0]


def test_embedded_reserve_does_not_depend_on_existing_worker_count():
    block = _embedded_block()
    assert "worker_count(" not in block
    assert "external_workers" not in block


def test_embedded_heartbeat_is_registered_before_tasks_start():
    block = _embedded_block()
    hb = block.index('await COORDINATOR.heartbeat(worker_id, "parser")')
    create = block.index("asyncio.create_task")
    assert hb < create


def test_version_417():
    assert (ROOT / "VERSION").read_text().strip() == "4.2.0"
    assert 'APP_VERSION = "4.2.0"' in BOT
