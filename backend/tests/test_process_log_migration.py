from datetime import timedelta
from sqlalchemy import select,text
from alembic import command
from alembic.config import Config
from backend.tests.test_execution_logs_postgres import pg,seed
from backend.app.core import ExecutionLogPolicy,ExecutionLogCatalog,LogReadRequest,LogReadSubscriber,RunLog,Run,Job,User,now
from backend.app.batch_deletion import remove


def test_process_log_migration_and_batch_deletion(pg,monkeypatch):
    engine,url=pg;monkeypatch.setenv('DATABASE_URL',url);cfg=Config('backend/alembic.ini')
    command.upgrade(cfg,'y518b90')
    factory=seed(engine,create=False)
    with factory() as s:s.add(RunLog(run_id='r',sequence=0,content='preserved'));s.commit()
    command.upgrade(cfg,'head')
    with factory() as s:
        assert s.get(RunLog,('r',0)).content=='preserved'
        s.add(ExecutionLogPolicy(run_id='r',snapshot={'revision':0}));s.add(ExecutionLogCatalog(id='log',run_id='r',execution_id='e',worker_id='w',slot_id='s',scope='business',episode_id=0,generation='g',root_path='/old/runs/e/logs'))
        s.commit()
        s.add(LogReadRequest(id='read',log_id='log',run_id='r',worker_id='w',budget_slot=0,fingerprint='f',generation='g',query={},expires_at=now()+timedelta(seconds=30)))
        s.commit();s.add(LogReadSubscriber(request_id='read',user_id='u',access_id='access'));s.commit()
        run=s.get(Run,'r');run.status='FAILED';run.cleanup_confirmed=True;s.get(Job,'j').status='FAILED';s.commit()
        assert remove(s,'b',s.get(User,'u'))['deleted'];s.commit()
        for model in [ExecutionLogPolicy,ExecutionLogCatalog,LogReadRequest,LogReadSubscriber]:assert not s.scalars(select(model)).all()
    command.downgrade(cfg,'y518b90')
    with engine.connect() as c:
        assert c.execute(text("SELECT to_regclass('execution_log_catalog')")).scalar() is None
        assert c.execute(text("SELECT to_regclass('run_logs')")).scalar()=='run_logs'
