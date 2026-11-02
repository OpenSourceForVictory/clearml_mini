"""Destructive migration checks: requires a dedicated empty database."""
import os
import pytest
from alembic.config import Config
from alembic import command
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from backend.app.core import session_factory, User, Pool, Job, Run

@pytest.mark.skipif(not os.getenv('ENDPOINT_MIGRATION_URL'),reason='dedicated migration database required')
def test_endpoint_backfill_constraint_and_downgrade_guard(monkeypatch):
    url=os.environ['ENDPOINT_MIGRATION_URL'];monkeypatch.setenv('DATABASE_URL',url)
    cfg=Config('backend/alembic.ini');command.upgrade(cfg,'head')
    factory=session_factory(url)
    with factory() as s:
        u=User(username='migration',password_hash='x');p=Pool(name='migration',gpu_model='virtual');s.add_all([u,p]);s.flush()
        for status in ['PENDING','RUNNING','SUCCEEDED']:
            j=Job(owner_id=u.id,pool_id=p.id,title=status,benchmark='cs',model_ref='m',episodes=1,
                  client_spec={'endpoint':'http://inference:8000','template':{'endpoint_exclusive':True}})
            s.add(j);s.flush();s.add(Run(job_id=j.id,attempt=1,status=status,execution_id=None if status=='PENDING' else status,cleanup_confirmed=status!='RUNNING'))
        s.commit()
    command.downgrade(cfg,'h841c23');command.upgrade(cfg,'head');command.check(cfg)
    with factory() as s:
        for r in s.scalars(select(Run)):
            assert r.assigned_endpoint==('http://inference:8000' if r.execution_id else None)
            assert r.assigned_endpoint_mode==('exclusive' if r.execution_id else None)
        with pytest.raises(IntegrityError):
            with s.begin_nested():s.execute(text("UPDATE evaluation_runs SET assigned_endpoint_mode='bad' WHERE status='RUNNING'"))
        j=s.scalar(select(Job).where(Job.title=='PENDING'));j.client_spec={'endpoints':[{'url':'http://inference:8000','mode':'shared'}]};s.commit()
    with pytest.raises(RuntimeError,match='Drain multi-address'):command.downgrade(cfg,'h841c23')
    with factory() as s:assert s.execute(text('SELECT version_num FROM alembic_version')).scalar()=='i952d34'
