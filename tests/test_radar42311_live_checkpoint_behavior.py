"""Executable SQL tests for v4.23.11 using an isolated in-memory SQLite DB.

The production repository uses asyncpg/aiosqlite. The audit environment has
neither driver, so this tiny adapter executes the exact SQLAlchemy statements
against synchronous SQLite. No network, Railway DB, or browser is involved.
"""
import ast
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
import logging

from sqlalchemy import and_, create_engine, select, update, delete, func, case, or_, text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert, dialect as pg_dialect
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
import pytest

from models import Base, RadarCheckpointEvent, RadarObservation, RadarProduct, RadarSnapshot
from radar_quality import ACTIVE_OBSERVATION_STATUSES

ROOT = Path(__file__).resolve().parents[1]
RADAR = (ROOT / "radar.py").read_text()


def load_functions(names, namespace):
    tree = ast.parse(RADAR)
    nodes = [node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names]
    assert {node.name for node in nodes} == set(names)
    module = ast.Module(body=[ast.ImportFrom(module="__future__", names=[ast.alias(name="annotations")], level=0), *nodes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), "radar.py", "exec"), namespace)
    return SimpleNamespace(**{name: namespace[name] for name in names})


class AsyncSyncSession:
    """Test-only adapter: actual SQL executes on SQLite, without fake rows."""
    def __init__(self, engine):
        self.session = Session(engine, expire_on_commit=False)
    async def __aenter__(self):
        return self
    async def __aexit__(self, exc_type, exc, tb):
        if exc_type:
            self.session.rollback()
        self.session.close()
    async def execute(self, statement, params=None):
        return self.session.execute(statement, params or {})
    async def commit(self):
        self.session.commit()
    async def get(self, *args):
        return self.session.get(*args)
    def get_bind(self):
        return self.session.get_bind()
    def add(self, value):
        self.session.add(value)


@pytest.fixture
def environment():
    engine = create_engine("sqlite:///:memory:")
    # Simulate an additive deployment: existing tables are created first, then
    # the new telemetry table is added without modifying/removing old rows.
    Base.metadata.create_all(engine, tables=[t for t in Base.metadata.tables.values() if t.name != "radar_checkpoint_events"])
    with Session(engine) as s:
        s.add(RadarObservation(external_id="legacy", category_key="el_handy", product_key="legacy",
            baseline_at=datetime.utcnow()-timedelta(days=2), last_measured_at=datetime.utcnow()-timedelta(days=2),
            baseline_views=3, last_views=3, status="expired"))
        s.commit()
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(RadarObservation)) == 1
    ns = dict(datetime=datetime, timedelta=timedelta, select=select, update=update, delete=delete,
        func=func, case=case, and_=and_, or_=or_, text=text, pg_insert=pg_insert, sqlite_insert=sqlite_insert,
        RadarCheckpointEvent=RadarCheckpointEvent, RadarObservation=RadarObservation,
        RadarProduct=RadarProduct, RADAR_V3_CHECKPOINT_AUDIT_DAYS=7, RADAR_V3_LIVE_RETENTION_HOURS=48,
        ACTIVE_OBSERVATION_STATUSES=ACTIVE_OBSERVATION_STATUSES,
        SessionLocal=lambda: AsyncSyncSession(engine), log=logging.getLogger("test-radar"))
    names = ["_radar_checkpoint_event_values", "_insert_radar_checkpoint_events", "_radar_exploration_count", "radar_v3_checkpoint_telemetry",
             "radar_v3_prune_checkpoint_events", "radar_v3_expire_observations", "radar_v3_expire_stale_products",
             "radar_v3_rollover_successful_category", "repair_radar_v3_depth_retirement_once"]
    funcs = load_functions(names, ns)
    yield engine, funcs
    engine.dispose()


def run(coro):
    return asyncio.run(coro)


def observation(ext, baseline, count=0, *, status="baseline", due=None, expires=None, delta=0):
    return RadarObservation(external_id=ext, category_key="el_handy", product_key=ext,
        baseline_at=baseline, last_measured_at=baseline, baseline_views=10, last_views=10+delta,
        checkpoint_count=count, total_delta=delta, status=status,
        next_check_at=due, expires_at=expires, created_at=baseline, updated_at=baseline)


def test_depth_absence_does_not_retire_and_age_expiry_preserves_score(environment):
    engine, f = environment
    now = datetime.utcnow()
    with Session(engine) as s:
        s.add_all([
            RadarProduct(product_key="recent", category_key="el_handy", status="hot", latest_source="radar3_observed",
                last_signal_at=now-timedelta(hours=3), current_score=91, peak_score=94, radar_rank=92),
            RadarProduct(product_key="old", category_key="el_handy", status="rising", latest_source="radar3_observed",
                last_signal_at=now-timedelta(hours=49), current_score=82, last_signal_score=82, peak_score=95, radar_rank=85),
            RadarProduct(product_key="other", category_key="el_handy", status="hot", latest_source="scan_hot",
                last_signal_at=now-timedelta(days=2), current_score=75, radar_rank=80),
        ])
        s.commit()
    assert run(f.radar_v3_rollover_successful_category("el_handy", [])) == 0
    assert run(f.repair_radar_v3_depth_retirement_once()) == 0
    assert run(f.radar_v3_expire_stale_products()) == 1
    with Session(engine) as s:
        products = {p.product_key:p for p in s.scalars(select(RadarProduct))}
        assert products["recent"].status == "hot" and products["recent"].current_score == 91
        assert products["old"].status == "historical" and products["old"].current_score == 82
        assert products["old"].peak_score == 95 and products["old"].radar_rank == 0
        assert products["other"].status == "hot"
    assert run(f.radar_v3_expire_stale_products()) == 0


def test_exact_journal_is_idempotent_and_counts_baseline_cycles(environment):
    engine, f = environment
    now = datetime.utcnow().replace(microsecond=0)
    first = now-timedelta(hours=3)
    obs = observation("cycle", first)
    events = [f._radar_checkpoint_event_values(obs, "baseline", now=first)]
    obs.checkpoint_count=1
    events.append(f._radar_checkpoint_event_values(obs, "measured", scheduled_at=first+timedelta(minutes=60),
        measured_at=first+timedelta(minutes=65), delta_views=5, now=first+timedelta(minutes=65)))
    obs.checkpoint_count=2
    events.append(f._radar_checkpoint_event_values(obs, "measured", scheduled_at=first+timedelta(minutes=120),
        measured_at=first+timedelta(minutes=135), delta_views=8, now=first+timedelta(minutes=135)))
    with Session(engine) as s:
        s.add(obs)
        s.commit()
    async def write():
        async with f._insert_radar_checkpoint_events.__globals__["SessionLocal"]() as s:
            await f._insert_radar_checkpoint_events(s, events)
            await f._insert_radar_checkpoint_events(s, events)
            await s.commit()
    run(write())
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(RadarCheckpointEvent)) == 3
        # A new baseline cycle for the same ID must not destroy the old funnel.
        s.add(observation("expired", first, expires=now-timedelta(minutes=1), due=first+timedelta(hours=1)))
        s.add(observation("quiet", first, count=1, status="quiet", delta=0))
        s.add(observation("leased", first, count=1, due=now-timedelta(minutes=70), expires=now+timedelta(hours=2)))
        s.add(observation("future", first, due=now+timedelta(minutes=10), expires=now+timedelta(hours=2)))
        s.commit()
    # Baselines and terminal outcomes are written explicitly; no fake samples.
    async def more():
        async with f._insert_radar_checkpoint_events.__globals__["SessionLocal"]() as s:
            for ext in ["expired", "quiet", "leased", "future"]:
                row = (await s.execute(select(RadarObservation).where(RadarObservation.external_id==ext))).scalar_one()
                await f._insert_radar_checkpoint_events(s, [f._radar_checkpoint_event_values(row, "baseline", now=first)])
                if ext=="quiet":
                    await f._insert_radar_checkpoint_events(s, [f._radar_checkpoint_event_values(row, "measured", scheduled_at=first+timedelta(hours=1), measured_at=first+timedelta(hours=1), delta_views=0, now=first+timedelta(hours=1)), f._radar_checkpoint_event_values(row, "quiet", now=first+timedelta(hours=1))])
            await s.commit()
    run(more())
    assert run(f.radar_v3_expire_observations()) == 1
    assert run(f.radar_v3_expire_observations()) == 0
    stats=run(f.radar_v3_checkpoint_telemetry())
    assert stats["cohort_baselines"] == 5
    assert stats["cohort_measured_once"] == 2
    assert stats["cohort_measured_twice"] == 1
    assert stats["cohort_growth"] == 1
    assert stats["cohort_quiet"] == 1
    assert stats["expired_before_first_24h"] == 1
    assert stats["expired_below_two_24h"] == 1
    assert stats["measured_events_24h"] == 3
    assert stats["positive_events_24h"] == 2
    assert stats["due"] == 1 and stats["late_60m"] == 1
    assert stats["scheduled"] == 1
    assert stats["oldest_due_seconds"] >= 70*60
    assert stats["lag_p50_seconds"] == 300
    assert stats["lag_p95_seconds"] > 800
    # Old cycles are excluded until their own baseline is actually logged.
    new_cycle=now-timedelta(minutes=5)
    with Session(engine) as s:
        row=s.scalar(select(RadarObservation).where(RadarObservation.external_id=="cycle"))
        row.baseline_at=new_cycle
        row.checkpoint_count=0
        row.total_delta=0
        s.commit()
    async def rearm():
        async with f._insert_radar_checkpoint_events.__globals__["SessionLocal"]() as s:
            row=(await s.execute(select(RadarObservation).where(RadarObservation.external_id=="cycle"))).scalar_one()
            await f._insert_radar_checkpoint_events(s,[f._radar_checkpoint_event_values(row,"baseline",now=new_cycle)])
            await s.commit()
    run(rearm())
    assert run(f.radar_v3_checkpoint_telemetry())["cohort_baselines"]==6


def test_retention_deletes_only_old_audit_events(environment):
    engine, f = environment
    now=datetime.utcnow()
    obs=observation("retention", now-timedelta(days=8))
    old=f._radar_checkpoint_event_values(obs,"baseline",now=now-timedelta(days=8))
    new=f._radar_checkpoint_event_values(obs,"measured",scheduled_at=now-timedelta(minutes=3),measured_at=now,delta_views=4,now=now)
    with Session(engine) as s:
        s.add(obs)
        s.commit()
    async def write():
        async with f._insert_radar_checkpoint_events.__globals__["SessionLocal"]() as s:
            await f._insert_radar_checkpoint_events(s,[old,new]);await s.commit()
    run(write())
    assert run(f.radar_v3_prune_checkpoint_events())==1
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(RadarCheckpointEvent))==1
        assert s.scalar(select(func.count()).select_from(RadarObservation))==2 # includes legacy



def test_expiry_respects_inflight_lease_and_preserves_on_time_evidence(environment):
    engine, f = environment
    now = datetime.utcnow()
    with Session(engine) as s:
        s.add(observation("inflight", now-timedelta(hours=6), count=1,
            due=now-timedelta(minutes=20), expires=now-timedelta(minutes=1)))
        s.commit()
        row=s.scalar(select(RadarObservation).where(RadarObservation.external_id=="inflight"))
        row.lease_owner="test-worker"
        row.lease_until=now+timedelta(minutes=5)
        s.commit()
    assert run(f.radar_v3_expire_observations()) == 0
    with Session(engine) as s:
        row=s.scalar(select(RadarObservation).where(RadarObservation.external_id=="inflight"))
        assert row.status=="baseline" and row.lease_owner=="test-worker"
        row.lease_until=now-timedelta(seconds=1)
        s.commit()
    assert run(f.radar_v3_expire_observations()) == 1
    with Session(engine) as s:
        row=s.scalar(select(RadarObservation).where(RadarObservation.external_id=="inflight"))
        assert row.status=="expired" and row.lease_owner==""
        event=s.scalar(select(RadarCheckpointEvent).where(RadarCheckpointEvent.external_id=="inflight"))
        assert event.checkpoint_no==1 and event.event_type=="expired"


def test_postgresql_event_upsert_compiles_without_network():
    stmt=pg_insert(RadarCheckpointEvent).values(external_id="x",category_key="el_handy",baseline_at=datetime.utcnow(),
        checkpoint_no=1,event_type="measured").on_conflict_do_nothing(constraint="uq_radar_checkpoint_event_cycle")
    sql=str(stmt.compile(dialect=pg_dialect()))
    assert "ON CONFLICT ON CONSTRAINT uq_radar_checkpoint_event_cycle DO NOTHING" in sql
    assert "radar_checkpoint_events" in sql


def test_checkpoint_growth_case_uses_one_integer_result():
    block = RADAR.split('async def radar_v3_checkpoint_telemetry', 1)[1].split(
        'async def radar_v3_prune_checkpoint_events', 1)[0]
    assert 'RadarCheckpointEvent.delta_views > 0,' in block
    assert '), 1), else_=0)).label("growth")' in block
