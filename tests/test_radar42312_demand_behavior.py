"""Offline behavioral regressions for v4.23.12. Real SQLAlchemy/SQLite writes;
no live marketplace, Railway, credentials or production database required.
"""
import ast
import asyncio
import logging
import math
from dataclasses import fields
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, select, update, delete, func, case, or_, text
from sqlalchemy.orm import Session
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from models import (Base, Listing, ListingIntegrity, RadarObservation, RadarCheckpointEvent,
    RadarProduct, RadarProductListing, RadarSnapshot, RadarLifecycleWatch, RadarLifecycleEvent)
from radar_quality import (ACTIVE_OBSERVATION_STATUSES, EXPLORATION_BATCH_LIMIT,
    ROLLBACK_RETRY_MINUTES, cohort_position, cohort_thresholds, qualifies_velocity,
    next_exploration_at, rollback_transition)
from tests.test_radar42311_live_checkpoint_behavior import AsyncSyncSession, load_functions, run

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT/'radar.py').read_text()


def listing(ext, at, views=10):
    return Listing(external_id=ext, category_key='el_handy', category='Elektronik',
        title='Evidence test', url='https://www.kleinanzeigen.de/s-anzeige/test/'+ext,
        price_eur=100, view_count=views, views_checked_at=at, last_seen_at=at,
        first_seen_at=at, is_active=True, is_promoted=False, is_price_reduced=False)


def observation(ext, at, views=10, status='baseline', count=0, expires=None):
    return RadarObservation(external_id=ext, category_key='el_handy', product_key=ext,
        baseline_views=10, baseline_at=at, last_views=views, last_measured_at=at,
        checkpoint_count=count, status=status, next_check_at=at+timedelta(hours=1),
        expires_at=expires or at+timedelta(hours=6), created_at=at, updated_at=at)


@pytest.fixture
def env():
    engine=create_engine('sqlite:///:memory:')
    Base.metadata.create_all(engine)
    ns=dict(datetime=datetime,timedelta=timedelta,select=select,update=update,delete=delete,
        func=func,case=case,or_=or_,text=text,pg_insert=pg_insert,sqlite_insert=sqlite_insert,
        Listing=Listing,ListingIntegrity=ListingIntegrity,RadarObservation=RadarObservation,
        RadarCheckpointEvent=RadarCheckpointEvent,RadarProduct=RadarProduct,
        RadarProductListing=RadarProductListing,RadarSnapshot=RadarSnapshot,
        RadarLifecycleWatch=RadarLifecycleWatch,RadarLifecycleEvent=RadarLifecycleEvent,
        SessionLocal=lambda:AsyncSyncSession(engine),log=logging.getLogger('test-radar42312'),
        ZoneInfo=__import__('zoneinfo').ZoneInfo,
        ACTIVE_OBSERVATION_STATUSES=ACTIVE_OBSERVATION_STATUSES,
        EXPLORATION_BATCH_LIMIT=EXPLORATION_BATCH_LIMIT,ROLLBACK_RETRY_MINUTES=ROLLBACK_RETRY_MINUTES,
        cohort_position=cohort_position,cohort_thresholds=cohort_thresholds,
        qualifies_velocity=qualifies_velocity,next_exploration_at=next_exploration_at,
        rollback_transition=rollback_transition,
        RADAR_V3_FIRST_CHECK_MINUTES=60,RADAR_V3_NEXT_CHECK_MINUTES=60,
        RADAR_V3_EARLY_CHECK_MINUTES=45,RADAR_V3_STRONG_CHECK_MINUTES=30,
        RADAR_V3_MAX_OBSERVATION_HOURS=6,RADAR_V3_LIVE_RETENTION_HOURS=48,
        RADAR_V3_CURRENT_SIGNAL_HOURS=6,RADAR_V3_NOISE_FLOOR_VPH=3.0,
        RADAR_V3_EARLY_PERCENTILE=.95,RADAR_V3_MIN_CATEGORY_PEERS=20,
        RADAR_V3_CHECKPOINT_AUDIT_DAYS=7,RADAR_SCAN_TOP_LIMIT=12,
        RADAR_LIFECYCLE_MIN_SCORE=72,RADAR_LIFECYCLE_EARLY_GLOBAL_CAP=300,
        RADAR_LIFECYCLE_EARLY_CATEGORY_CAP=8,RADAR_LIFECYCLE_EARLY_MIN_VIEWS=15,
        RADAR_LIFECYCLE_EARLY_MAX_INITIAL_VIEWS=399,
        RADAR_LIFECYCLE_CHECK_MINUTES=(15,30,60,120,180),
        RADAR_LIFECYCLE_CONFIRM_MINUTES=3,RADAR_LIFECYCLE_UNKNOWN_RETRY_MINUTES=5,
        RADAR_LIFECYCLE_MAX_MINUTES=180,RADAR_FAST_SOLD_MAX_SECONDS=10800,
        DATABASE_BACKEND='SQLite (local development)',
        radar_v3_category_allowed=lambda key:True,radar_product_key=lambda item:item.external_id,
        _registry_dirty_exists=lambda expr:select(ListingIntegrity.external_id).where(False).exists(),
        _clean_listing_exists=lambda expr:select(Listing.external_id).where(Listing.external_id==expr,
            Listing.is_promoted.is_(False),Listing.is_price_reduced.is_(False)).exists())
    async def gate(*args,**kwargs):return True,'organic',datetime.utcnow()
    ns['_live_detail_organic_gate']=gate
    ns['_strict_organic_gate']=lambda *args,**kwargs:None
    async def strict(*args,**kwargs):return True,'organic'
    ns['_strict_organic_gate']=strict
    emitted=[]
    async def upsert(**kwargs):
        emitted.append(kwargs)
        return 1
    ns['_upsert_signal']=upsert
    names=['_radar_checkpoint_event_values','_insert_radar_checkpoint_events',
        '_radar_exploration_count','_radar_quarantine_rollback','_radar_reset_observation_cycle',
        '_radar32_thresholds','_percentile_rank','_observed_signal_matches',
        'radar_v3_record_refreshed','radar_v3_claim_due_external_ids','radar_v3_release_claims',
        '_next_lifecycle_checkpoint','_lifecycle_event','_maybe_queue_early_lifecycle',
        '_maybe_queue_lifecycle_watch','_lifecycle_reason','complete_lifecycle_check',
        '_snapshot_live_evidence','radar_v3_expire_stale_products']
    # Bind source functions in the same namespace so their actual internal calls work.
    class SessionWithExpunge(AsyncSyncSession):
        def expunge(self, value): self.session.expunge(value)
        async def flush(self): self.session.flush()
    ns['SessionLocal']=lambda:SessionWithExpunge(engine)
    funcs=load_functions(names,ns)
    ns['RadarRankEvidence']=__import__('radar_ranking').RadarRankEvidence
    ns['classify_radar_signal']=__import__('radar_ranking').classify_radar_signal
    ns['_clamp_score']=lambda x:max(0,min(100,int(x or 0)))
    ns['RADAR_48H_MAX_AGE_MINUTES']=48*60
    yield engine,funcs,ns,emitted
    engine.dispose()


def test_baseline_count_is_new_cycles_not_all_eligible(env):
    engine,f,ns,_=env
    # Load actual seeder and its declared admission result type.
    ns['dataclass']=__import__('dataclasses').dataclass
    tree=ast.parse(SOURCE)
    node=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='RadarAdmissionStats')
    exec(compile(ast.fix_missing_locations(ast.Module(body=[node],type_ignores=[])),'radar.py','exec'),ns)
    seed=load_functions(['record_autoscan_hot_detailed'],ns).record_autoscan_hot_detailed
    async def no_early(*args,**kwargs):return False
    ns['_maybe_queue_early_lifecycle']=no_early
    now=datetime.utcnow().replace(microsecond=0)
    with Session(engine) as s:s.add(listing('same',now));s.commit()
    first=run(seed('r1','el_handy',['same']))
    second=run(seed('r2','el_handy',['same']))
    assert first.baseline_created==1 and first.baseline_existing==0
    assert second.baseline_created==0 and second.baseline_existing==1
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(RadarObservation))==1
        assert s.scalar(select(func.count()).select_from(RadarCheckpointEvent).where(
            RadarCheckpointEvent.event_type=='baseline'))==1


def test_weak_first_interval_continues_then_late_growth_is_evaluated(env):
    engine,f,ns,emitted=env
    now=datetime.utcnow().replace(microsecond=0)
    start=now-timedelta(minutes=65)
    with Session(engine) as s:
        s.add(listing('late',now,15));s.add(observation('late',start));s.commit()
    assert run(f.radar_v3_record_refreshed(['late']))==0
    with Session(engine) as s:
        obs=s.scalar(select(RadarObservation).where(RadarObservation.external_id=='late'))
        assert obs.status=='exploring' and obs.next_check_at is not None
        assert obs.last_views==15 and obs.checkpoint_count==1
        # Time-travel only the recorded sample, not the production clock.
        item=s.scalar(select(Listing).where(Listing.external_id=='late'))
        item.view_count=55;item.views_checked_at=now+timedelta(minutes=60)
        s.commit()
    # The new sample must belong to an unexpired observation; use a fixed
    # six-hour window and actual source processing rather than mocking Score.
    assert run(f.radar_v3_record_refreshed(['late']))>=0
    with Session(engine) as s:
        obs=s.scalar(select(RadarObservation).where(RadarObservation.external_id=='late'))
        assert obs.checkpoint_count==2 and obs.last_views==55
        assert obs.current_vph>30 and obs.total_delta==45
        assert obs.status in {'candidate','observed','confirmed'}


def test_rollback_never_lowers_trusted_anchor_or_emits_old_score(env):
    engine,f,ns,emitted=env
    now=datetime.utcnow().replace(microsecond=0)
    start=now-timedelta(hours=2)
    with Session(engine) as s:
        s.add(listing('rollback',now,90))
        s.add(observation('rollback',start,110,status='observed',count=2,expires=now+timedelta(hours=4)))
        s.commit()
    run(f.radar_v3_record_refreshed(['rollback']))
    with Session(engine) as s:
        obs=s.scalar(select(RadarObservation).where(RadarObservation.external_id=='rollback'))
        assert obs.status=='rollback_pending' and obs.last_views==110
        assert obs.baseline_views==10 and obs.rollback_last_views==90
        assert not f._observed_signal_matches(obs,obs.last_measured_at,110)
    for minute,value in [(10,95),(20,115),(30,120)]:
        with Session(engine) as s:
            item=s.scalar(select(Listing).where(Listing.external_id=='rollback'))
            item.view_count=value;item.views_checked_at=now+timedelta(minutes=minute)
            s.commit()
        run(f.radar_v3_record_refreshed(['rollback']))
    with Session(engine) as s:
        obs=s.scalar(select(RadarObservation).where(RadarObservation.external_id=='rollback'))
        assert obs.status=='baseline' and obs.last_views==120 and obs.baseline_views==120
        assert obs.checkpoint_count==0 and obs.total_delta==0
        assert obs.provenance_reset_at==obs.baseline_at
    assert emitted==[]


def test_flat_cohort_does_not_invent_winner_and_sparse_has_absolute_floor():
    flat=[10.0]*20; t=cohort_thresholds(flat)
    assert not qualifies_velocity(10,flat,'candidate',t)
    assert not qualifies_velocity(10,flat,'hot',t)
    peers=list(range(20))+[80.0]
    assert qualifies_velocity(80,peers,'candidate')
    assert qualifies_velocity(80,peers,'early')
    assert qualifies_velocity(80,peers,'strong')
    assert qualifies_velocity(80,peers,'hot')
    assert not qualifies_velocity(4,[0,1,2,3,4],'candidate')
    assert qualifies_velocity(65,[0,1,2,3,65],'strong')


def test_claims_are_unique_and_exploration_has_bounded_quota(env):
    engine,f,ns,_=env
    now=datetime.utcnow().replace(microsecond=0)
    with Session(engine) as s:
        s.add_all([observation(f'p{i}',now-timedelta(hours=1),status='confirmed',count=2)
                   for i in range(12)])
        s.add_all([observation(f'e{i}',now-timedelta(hours=1),status='exploring',count=1)
                   for i in range(12)])
        s.commit()
    claimed=run(f.radar_v3_claim_due_external_ids('owner-a',limit=8))
    assert len(claimed)==len(set(claimed))==8
    assert len([x for x in claimed if x.startswith('e')])<=1
    assert run(f.radar_v3_claim_due_external_ids('owner-b',limit=8))
    assert not set(claimed).intersection(run(f.radar_v3_claim_due_external_ids('owner-b',limit=8)))
    assert run(f.radar_v3_release_claims('wrong-owner',claimed))==0
    assert run(f.radar_v3_release_claims('owner-a',claimed))==8


def test_48h_retention_is_distinct_from_current_hot(env):
    _,f,_,_=env
    rank=__import__('radar_ranking').RadarRankEvidence
    now=datetime.utcnow()
    for age,status in [(5,'hot'),(7,'stable'),(47,'stable'),(49,'historical')]:
        snap=SimpleNamespace(source='radar3_observed',recorded_at=now-timedelta(hours=age),
            demand_age_minutes=60,demand_status='hot',radar_rank=95,score=90)
        result=f._snapshot_live_evidence(snap,now)
        assert result.status==status
        assert result.admitted==(age<=48)


def test_early_lifecycle_requires_actual_strong_qualification_and_two_misses(env):
    engine,f,ns,_=env
    now=datetime.utcnow().replace(microsecond=0)
    first=now-timedelta(minutes=20)
    with Session(engine) as s:s.add(listing('early',first,40));s.commit()
    budget={'total':0,'category':0}
    async def enroll():
        async with ns['SessionLocal']() as s:
            item=(await s.execute(select(Listing))).scalar_one()
            result=await f._maybe_queue_early_lifecycle(s,item,now,budget=budget)
            await s.commit();return result
    assert run(enroll())
    with Session(engine) as s:
        watch=s.scalar(select(RadarLifecycleWatch))
        assert watch.product_id is None and watch.peak_score==0
        watch_id=watch.id
    assert run(f.complete_lifecycle_check(watch_id,None,checked_at=now))=='watching'
    assert run(f.complete_lifecycle_check(watch_id,False,checked_at=now+timedelta(minutes=2)))=='confirming'
    # An UNKNOWN does not count as a second disappearance.
    assert run(f.complete_lifecycle_check(watch_id,None,checked_at=now+timedelta(minutes=3)))=='confirming'
    assert run(f.complete_lifecycle_check(watch_id,False,checked_at=now+timedelta(minutes=6)))=='disappeared'
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(RadarSnapshot))==0
        assert s.scalar(select(func.count()).select_from(RadarLifecycleEvent).where(
            RadarLifecycleEvent.event_type=='disappeared'))==1
        assert s.scalar(select(Listing.is_active).where(Listing.external_id=='early')) is False


def test_strong_link_preserves_first_qualification_and_late_enrollment_is_skipped(env):
    engine,f,ns,_=env
    now=datetime.utcnow().replace(microsecond=0)
    with Session(engine) as s:
        item=listing('qualified',now-timedelta(hours=1),50)
        product=RadarProduct(product_key='qualified',category_key='el_handy',last_signal_at=now)
        s.add_all([item,product]);s.flush();product_id=product.id
        s.add(RadarLifecycleWatch(product_id=None,product_key='qualified',external_id='qualified',
            category_key='el_handy',first_seen_at=item.first_seen_at,status='watching',
            enrollment_source='early',score=0,peak_score=0,next_check_at=now+timedelta(minutes=5)))
        s.commit()
    async def link(at):
        async with ns['SessionLocal']() as s:
            item=(await s.execute(select(Listing))).scalar_one()
            product=await s.get(RadarProduct,product_id)
            await f._maybe_queue_lifecycle_watch(s,product=product,listing=item,score=80,now=at,demand_status='rising')
            await s.commit()
    run(link(now));run(link(now+timedelta(minutes=5)))
    with Session(engine) as s:
        watch=s.scalar(select(RadarLifecycleWatch))
        assert watch.product_id==product_id and watch.strong_qualified_at==now
        assert watch.peak_score==80 and watch.enrollment_source=='early'


def test_source_provenance_requires_current_checkpoint():
    now=datetime.utcnow().replace(microsecond=0)
    obs=observation('proof',now-timedelta(hours=1),110,status='observed',count=2)
    ns={'RadarObservation':RadarObservation}
    f=load_functions(['_observed_signal_matches'],ns)._observed_signal_matches
    assert f(obs,obs.last_measured_at,110)
    obs.provenance_reset_at=now
    assert not f(obs,obs.last_measured_at,110)
    obs.baseline_at=obs.last_measured_at=now
    obs.baseline_views=obs.last_views=120;obs.checkpoint_count=0;obs.status='baseline'
    assert not f(obs,now,120)


def test_additive_migration_preserves_rows_and_null_current_evidence():
    """Execute the actual v4.23.12 DDL block twice against a pre-upgrade schema."""
    from sqlalchemy import inspect
    engine=create_engine('sqlite:///:memory:')
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE radar_products (id INTEGER PRIMARY KEY, last_signal_at TIMESTAMP, latest_source VARCHAR(32), status VARCHAR(24))"))
        conn.execute(text("CREATE TABLE radar_lifecycle_watches (id INTEGER PRIMARY KEY, product_id INTEGER NOT NULL, external_id VARCHAR(64), peak_score INTEGER)"))
        conn.execute(text("INSERT INTO radar_products VALUES (1,'2026-09-08 10:00:00','radar3_observed','hot')"))
        conn.execute(text("INSERT INTO radar_lifecycle_watches VALUES (1,1,'existing',80)"))
    # init_db creates new tables before executing its additive ALTER block.
    Base.metadata.create_all(engine, tables=[RadarLifecycleEvent.__table__])
    source=(ROOT/'db.py').read_text()
    start=source.index('        # Retention time and the selected current evidence are independent.')
    end=source.index('        # v4.21.1 Radar 3.0 cross-replica observation leases.',start)
    block=source[start:end]
    code='async def migrate(conn):\n'+block
    ns={'text':text,'_IS_POSTGRES':False,'_table_columns':lambda c,t:
        {x['name'] for x in inspect(c).get_columns(t)} if t in inspect(c).get_table_names() else set()}
    exec(compile(code,'db.py','exec'),ns)
    class Conn:
        def __init__(self,c):self.c=c
        async def run_sync(self,fn):return fn(self.c)
        async def execute(self,stmt):return self.c.execute(stmt)
    with engine.begin() as c:run(ns['migrate'](Conn(c)))
    with engine.begin() as c:
        assert c.execute(text('SELECT current_signal_at FROM radar_products WHERE id=1')).scalar() is not None
        c.execute(text('UPDATE radar_products SET current_signal_at=NULL WHERE id=1'))
    with engine.begin() as c:run(ns['migrate'](Conn(c)))
    with engine.begin() as c:
        assert c.execute(text('SELECT current_signal_at FROM radar_products WHERE id=1')).scalar() is None
        assert c.execute(text('SELECT COUNT(*) FROM radar_lifecycle_watches')).scalar()==1
        assert c.execute(text('SELECT COUNT(*) FROM radar_lifecycle_events')).scalar()==0
        assert {'product_key','enrollment_source','strong_qualified_at'} <= {x['name'] for x in inspect(c).get_columns('radar_lifecycle_watches')}
        assert c.execute(text('SELECT COUNT(*) FROM radar_lifecycle_events')).scalar()==0
    engine.dispose()


def test_postgresql_migration_compiles_and_uses_additive_fields():
    from sqlalchemy.dialects import postgresql
    sqls=[
        'ALTER TABLE radar_products ADD COLUMN IF NOT EXISTS current_signal_at TIMESTAMP',
        'ALTER TABLE radar_lifecycle_watches ALTER COLUMN product_id DROP NOT NULL',
        'ALTER TABLE radar_lifecycle_watches ADD COLUMN IF NOT EXISTS strong_qualified_at TIMESTAMP',
    ]
    for sql in sqls:
        assert str(text(sql).compile(dialect=postgresql.dialect()))==sql
    # No destructive mutation of the observation/snapshot tables is part of DDL.
    source=(ROOT/'db.py').read_text()
    block=source[source.index('        # Retention time and the selected current evidence are independent.'):source.index('        # v4.21.1 Radar 3.0 cross-replica observation leases.')]
    assert 'DELETE FROM' not in block and 'DROP TABLE' not in block


def test_family_current_signal_and_availability_are_independent(env):
    engine,f,ns,_=env
    import json
    ns.update(json=json, _radar_lock=asyncio.Lock(),
        _feature_for_listing=lambda *a,**kw:None,
        _clamp_score=lambda x:max(0,min(100,int(x or 0))),
        _maybe_queue_lifecycle_watch=None)
    async def no_lifecycle(*args,**kwargs):return None
    ns['_maybe_queue_lifecycle_watch']=no_lifecycle
    ns['classify_radar_signal']=__import__('radar_ranking').classify_radar_signal
    ns['RadarRankEvidence']=__import__('radar_ranking').RadarRankEvidence
    ns['_effective_score']=lambda p,now:int(p.last_signal_score or 0)
    load_functions(['_registry_dirty_exists','_clean_listing_exists','_active_radar_listing_exists','_radar_provenance_pending'],ns)
    now=datetime.utcnow().replace(microsecond=0)
    a=now-timedelta(hours=2);b=now-timedelta(hours=1)
    with Session(engine) as s:
        for ext,at,views in [('a',a,90),('b',b,30)]:
            item=listing(ext,at,views);item.identity_label='Shared family'
            s.add(item);s.add(observation(ext,at,views,status='confirmed',count=2,expires=now+timedelta(hours=4)))
        s.commit()
    async def persist(ext,at,score,status):
        async with ns['SessionLocal']() as s:
            item=(await s.execute(select(Listing).where(Listing.external_id==ext))).scalar_one()
            return await ns['_upsert_signal'](source_key=f'test:{ext}:{at.timestamp()}',source='radar3_observed',
                listing=item,product_key='shared-family',score=score,confidence=80,stage='confirmed',
                opportunity_type='observed_demand',view_count=item.view_count,views_per_hour=30,
                recorded_at=at,live_detail_verified_at=now,demand_views=20,demand_age_minutes=60,
                radar_rank=score,demand_status=status)
    ns['RADAR_V3_LIVE_RETENTION_HOURS']=48
    ns['RADAR_V3_CURRENT_SIGNAL_HOURS']=6
    ns['RADAR_LIFECYCLE_MIN_SCORE']=72
    ns['RADAR_FAST_SOLD_MAX_SECONDS']=10800
    # Execute actual source upsert with a no-op availability enroller, so this
    # test is restricted to aggregation rather than creating artificial watches.
    load_functions(['_refresh_family_from_snapshots','_upsert_signal','refresh_radar_scores'],ns)
    # Both listings refer to one family, with independent exact observations.
    run(persist('a',a,90,'hot'))
    run(persist('b',b,50,'stable'))
    with Session(engine) as s:
        product=s.scalar(select(RadarProduct).where(RadarProduct.product_key=='shared-family'))
        assert product.status=='hot' and product.current_signal_at==a
        assert product.last_signal_at==b and product.peak_score==90
        product_id=product.id
        first=s.scalar(select(Listing).where(Listing.external_id=='a'))
        first.is_active=False
        s.add(RadarLifecycleWatch(product_id=product_id,product_key='shared-family',external_id='a',
            category_key='el_handy',first_seen_at=a,status='disappeared',peak_score=90,
            strong_qualified_at=a,disappeared_at=now,lifetime_seconds=7200))
        s.commit()
    run(ns['refresh_radar_scores']())
    with Session(engine) as s:
        product=s.get(RadarProduct,product_id)
        assert product.status=='stable'
        assert product.representative_external_id=='b'
        assert product.current_signal_at==b
        assert product.last_signal_at==b
        assert product.peak_score==90
    # A verified missing member must not hide the other active listing.
    assert product_id in [p.id for p in Session(engine).scalars(select(RadarProduct).where(RadarProduct.status!='historical'))]
