"""Real PostgreSQL observation/configuration isolation and reversible migration."""
import uuid
import pytest

from alembic import command
from alembic.config import Config
from sqlalchemy import select, text

from backend.app.core import Worker, AgentObservation
from backend.app.agent_state import record
from backend.tests.test_db_budget import online, pg
from backend.tests.test_execution_logs_postgres import seed


def register(client, headers):
    reply = client.post('/api/v1/agent/register', headers=headers,
                        json={'boot_id': uuid.uuid4().hex, 'version':'test'})
    assert reply.status_code == 200, reply.text
    return reply.json()['session']


def report(client, headers, session, sequence):
    return client.post('/api/v1/agent/heartbeat', headers=headers,
                       json={'session':session, 'sequence':sequence,
                             'applied_revision':0, 'config_status':'applied'})


def test_configuration_lock_does_not_block_latest_observation(online, pg):
    main, client, headers = online
    session = register(client, headers)
    from sqlalchemy.orm import Session
    with Session(pg[0]) as configure:
        worker = configure.scalar(select(Worker).where(Worker.id=='w').with_for_update())
        worker.connection={**worker.connection, 'agent':{**worker.connection['agent'], 'revision':1}}
        configure.flush()
        response=report(client, headers, session, 1)
        assert response.status_code==200, response.text
        configure.commit()
    with main.SessionLocal() as db:
        state=record(db.get(Worker,'w'))
        assert state['revision']==1 and state['sequence']==1
        assert state['applied_revision']==0  # Does not falsely acknowledge the new configuration.


def test_old_session_and_sequence_cannot_overwrite_current_report(online):
    main, client, headers=online
    old=register(client,headers)
    assert report(client,headers,old,9).status_code==200
    current=register(client,headers)
    assert report(client,headers,old,10).status_code==409
    assert report(client,headers,current,2).status_code==200
    assert report(client,headers,current,1).status_code==200
    with main.SessionLocal() as db:
        state=record(db.get(Worker,'w'))
        assert state['session']==current and state['sequence']==2


def test_observation_migration_roundtrip_preserves_current_configuration(pg,monkeypatch):
    engine,url=pg
    monkeypatch.setenv('DATABASE_URL',url)
    cfg=Config('backend/alembic.ini')
    command.upgrade(cfg,'ac963d4')
    factory=seed(engine,create=False)
    with factory.begin() as db:
        db.get(Worker,'w').connection={'unrelated':True,'agent':{
            'session':'original','revision':3,'desired':{'programs':'running'},
            'sequence':4,'seen_at':'2026-11-23T00:00:00+00:00','samples':{}}}
    command.upgrade(cfg,'ad074e5')
    with factory.begin() as db:
        worker=db.get(Worker,'w')
        assert record(worker)['sequence']==4
        report=db.get(AgentObservation,'w')
        report.payload={**report.payload,'sequence':5}
        worker.connection={**worker.connection,'agent':{**worker.connection['agent'],'revision':6}}
    command.downgrade(cfg,'ac963d4')
    with factory() as db:
        config=db.get(Worker,'w').connection
        assert config['unrelated'] is True
        assert config['agent']['revision']==6 and config['agent']['sequence']==5
        assert db.scalar(text("SELECT to_regclass('node_agent_observations')")) is None
    command.upgrade(cfg,'ad074e5')
    with factory() as db:
        assert record(db.get(Worker,'w'))['sequence']==5


def test_empty_database_observation_migration_roundtrip(pg, monkeypatch):
    engine, url = pg
    monkeypatch.setenv('DATABASE_URL', url)
    cfg = Config('backend/alembic.ini')
    command.upgrade(cfg, 'ad074e5')
    with engine.connect() as db:
        assert db.scalar(text("SELECT to_regclass('node_agent_observations')")) is not None
        assert db.scalar(text('SELECT count(*) FROM node_agent_observations')) == 0
    command.downgrade(cfg, 'ac963d4')
    with engine.connect() as db:
        assert db.scalar(text("SELECT to_regclass('node_agent_observations')")) is None
    command.upgrade(cfg, 'ad074e5')
    with engine.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM node_agent_observations')) == 0


def test_downgrade_does_not_restore_superseded_session(pg,monkeypatch):
    engine,url=pg
    monkeypatch.setenv('DATABASE_URL',url)
    cfg=Config('backend/alembic.ini')
    command.upgrade(cfg,'ad074e5')
    factory=seed(engine,create=False)
    with factory.begin() as db:
        db.get(Worker,'w').connection={'agent':{'session':'new','sequence':0,'revision':1}}
        db.add(AgentObservation(worker_id='w',session='old',payload={'sequence':99}))
    command.downgrade(cfg,'ac963d4')
    with factory() as db:
        assert db.get(Worker,'w').connection['agent']['sequence']==0


def test_detached_legacy_snapshot_does_not_query_observation():
    from types import SimpleNamespace
    agent={'session':'legacy','sequence':3}
    assert record(SimpleNamespace(connection={'agent':agent})) is agent


@pytest.mark.parametrize('observed_session',[None,'superseded'])
def test_missing_or_superseded_observation_uses_current_config(pg,observed_session):
    factory=seed(pg[0])
    with factory.begin() as db:
        db.get(Worker,'w').connection={'agent':{'session':'current','sequence':0}}
        if observed_session:
            db.add(AgentObservation(worker_id='w',session=observed_session,payload={'sequence':99}))
    with factory() as db:
        assert record(db.get(Worker,'w'))=={'session':'current','sequence':0}


def test_failed_backfill_rolls_back_schema_and_can_retry(pg,monkeypatch):
    engine,url=pg
    monkeypatch.setenv('DATABASE_URL',url)
    cfg=Config('backend/alembic.ini')
    command.upgrade(cfg,'ac963d4')
    factory=seed(engine,create=False)
    with factory.begin() as db:
        db.get(Worker,'w').connection={'agent':['invalid historical shape']}
    with pytest.raises(AttributeError):
        command.upgrade(cfg,'ad074e5')
    with factory() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='ac963d4'
        assert db.scalar(text("SELECT to_regclass('node_agent_observations')")) is None
    with factory.begin() as db:
        db.get(Worker,'w').connection={'agent':{'session':'repaired','sequence':3}}
    command.upgrade(cfg,'ad074e5')
    with factory() as db:
        assert record(db.get(Worker,'w'))['sequence']==3
