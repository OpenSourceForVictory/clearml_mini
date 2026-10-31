"""Run against a dedicated migrated disposable PostgreSQL database."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from datetime import timedelta
import pytest
from sqlalchemy import select
from backend.app.core import User, Pool, Slot, Worker, Run, DataCleanupItem, DataCleanup, session_factory, now
from backend.tests.test_execution import env
from backend.tests.test_retention import setup, preview

@pytest.mark.skipif(not os.getenv('RETENTION_DATABASE_URL'),reason='dedicated PostgreSQL required')
def test_postgres_confirm_replay_policy_begin_and_storage_serialization(env,monkeypatch):
    main,c,auth=env
    factory=session_factory(os.environ['RETENTION_DATABASE_URL'])
    with main.SessionLocal() as src, factory() as dst:
        for u in src.scalars(select(User)):
            dst.add(User(id=u.id,username=u.username,role=u.role,password_hash=u.password_hash))
        dst.add(Pool(id='mock',name='mock',gpu_model='virtual'));dst.commit()
    monkeypatch.setattr(main,'SessionLocal',factory)
    main,c,auth,token,slot,base,control,entry,data,job=setup(env)
    def race(*functions):
        barrier=Barrier(len(functions))
        def call(fn):barrier.wait();return fn()
        with ThreadPoolExecutor(max_workers=len(functions)) as executor:
            return list(executor.map(call,functions))
    op=preview(c,auth,base,data)
    confirm=lambda:c.post(base+f'/execution-data/operations/{op["id"]}/confirm',headers=auth['admin'])
    results=race(confirm,confirm)
    assert [r.status_code for r in results]==[202,202]
    assert results[0].json()['id']==results[1].json()['id']
    retried,replayed=race(lambda:c.post(f'/api/v1/jobs/{job}/retry',headers=auth['alice']),confirm)
    assert retried.status_code==202 and replayed.status_code==202
    with factory() as s:
        attempts=list(s.scalars(select(Run).where(Run.job_id==job).order_by(Run.attempt)))
        assert len(attempts)==2 and attempts[0].execution_id==entry['entry_name'] and attempts[1].execution_id is None
    controls=race(lambda:c.post(control+'/control',headers=token),lambda:c.post(control+'/control',headers=token))
    item=controls[0].json()['items'][0]
    assert controls[0].json()['items']==controls[1].json()['items']
    begin=lambda:c.post(control+f'/{item["id"]}/begin',headers=token)
    storage=lambda:c.put(base+'/storage',headers=auth['admin'],json={'expected_revision':0,'config':None})
    start,saved=race(begin,storage)
    assert saved.status_code==200 and start.status_code in (200,409)
    if start.status_code==200:
        assert c.post(control+f'/{item["id"]}/receipt',headers=token,json={'status':'skipped','reason':'test'}).status_code==200
    # Reset acknowledgement to test policy race, after serial storage mutation.
    with factory() as s:
        s.get(Slot,slot).storage_applied_revision=1;s.commit()
    policy={'expected_revision':0,'enabled':True,'days':30}
    p=c.post(base+'/retention/preview',headers=auth['admin'],json=policy).json()
    assert c.put(base+'/retention',headers=auth['admin'],json={**policy,'preview_id':p['id']}).status_code==200
    item=c.post(control+'/control',headers=token).json()['items'][0]
    disable=lambda:c.put(base+'/retention',headers=auth['admin'],json={'expected_revision':1,'enabled':False,'days':30})
    start,disabled=race(begin,disable)
    assert disabled.status_code==200 and start.status_code in (200,409)
    with factory() as s:
        current=s.get(DataCleanupItem,item['id'])
        assert current.status==('running' if start.status_code==200 else 'skipped')
        assert not s.get(Worker,s.get(Slot,slot).worker_id).retention_enabled
        # Short endpoint transactions have completed: no scheduler lock is held.
        from sqlalchemy import text
        assert s.execute(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND state='idle in transaction' AND pid<>pg_backend_pid()" )).scalar()==0
