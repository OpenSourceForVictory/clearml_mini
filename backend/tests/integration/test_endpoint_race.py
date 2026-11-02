"""Dedicated PostgreSQL: endpoint admissions and lifecycle transactions race."""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import select
from backend.app.core import User, Pool, Run, session_factory
from backend.app.scheduling import tick, transition, lock
from backend.tests.test_execution import env
from backend.tests.test_endpoints import setup, submit, A, B

@pytest.mark.skipif(not os.getenv('ENDPOINT_DATABASE_URL'), reason='dedicated PostgreSQL required')
def test_endpoint_scheduler_claim_cancel_cleanup_retry_races(env, monkeypatch):
    main,c,auth=env
    factory=session_factory(os.environ['ENDPOINT_DATABASE_URL'])
    with main.SessionLocal() as src, factory() as dst:
        for u in src.scalars(select(User)):
            dst.add(User(id=u.id,username=u.username,role=u.role,password_hash=u.password_hash))
        dst.add(Pool(id='mock',name='mock',gpu_model='virtual'));dst.commit()
    monkeypatch.setattr(main,'SessionLocal',factory)
    main,c,auth,t,token,slots=setup(env,monkeypatch,count=4)
    def race(*functions):
        barrier=Barrier(len(functions))
        def call(fn):barrier.wait();return fn()
        with ThreadPoolExecutor(max_workers=len(functions)) as executor:return list(executor.map(call,functions))
    jobs=[submit(c,auth,t,[(A,'exclusive'),(B,'exclusive')],owner) for owner in ['alice','bob','alice','bob']]
    race(*[lambda:tick(factory) for _ in range(6)])
    with factory() as s:
        active=list(s.scalars(select(Run).where(Run.status=='RESERVED')))
        assert len(active)==2 and {r.assigned_endpoint for r in active}=={A,B}
        sid,ex,jid=active[0].slot_id,active[0].execution_id,active[0].job_id
    claim=lambda:c.post(f'/api/v1/worker/slots/{sid}/claim',headers=token)
    result=race(claim,claim)
    assert result[0].json()['task']==result[1].json()['task']
    url=result[0].json()['task']['client_spec']['endpoint']
    race(claim,lambda:c.post(f'/api/v1/jobs/{jid}/cancel',headers=auth['admin']))
    with factory() as s:
        lock(s);r=s.scalar(select(Run).where(Run.execution_id==ex));assert r.assigned_endpoint==url
        transition(s,r,'CANCELLED');s.commit()
    assert tick(factory)==0
    cleanup=lambda:c.post(f'/api/v1/worker/executions/{ex}/cleanup',headers=token)
    results=race(cleanup,lambda:tick(factory),lambda:c.post(f'/api/v1/jobs/{jid}/retry',headers=auth['admin']))
    assert results[0].status_code==200 and results[2].status_code in (202,409)
    if results[2].status_code==409:
        assert c.post(f'/api/v1/jobs/{jid}/retry',headers=auth['admin']).status_code==202
    tick(factory)
    with factory() as s:
        active=list(s.scalars(select(Run).where(Run.status.in_(['RESERVED','STARTING','RUNNING']))))
        assert len(active)==2 and len({r.assigned_endpoint for r in active})==2
        old=s.scalar(select(Run).where(Run.execution_id==ex));assert old.assigned_endpoint==url and old.cleanup_confirmed
    # Pause vs claim is serialized: either no claim and binding cleared, or a fixed running attempt.
    for job in jobs:
        c.post(f'/api/v1/jobs/{job}/cancel',headers=auth['admin'])
    from backend.tests.test_endpoints import body
    value={'title':'pause-race','pool_id':'mock','max_concurrent':1,'cases':[{'case_id':'x','task_name':'demo','episodes':1,'seed':0}],
           'client':body(t,[(A,'exclusive'),(B,'exclusive')])['client']}
    batch=c.post('/api/v1/batches',headers=auth['alice'],json=value).json();tick(factory)
    jid=batch['cases'][0]['job_id']
    with factory() as s:r=s.scalar(select(Run).where(Run.job_id==jid));sid=r.slot_id
    assert sid
    result=race(lambda:c.post(f'/api/v1/worker/slots/{sid}/claim',headers=token),lambda:c.post(f'/api/v1/batches/{batch["id"]}/control',headers=auth['alice'],json={'action':'pause','expected_revision':0}))
    assert all(r.status_code==200 for r in result)
    with factory() as s:
        r=s.scalar(select(Run).where(Run.job_id==jid));assert (r.status=='PENDING' and r.assigned_endpoint is None) or (r.status=='STARTING' and r.assigned_endpoint in (A,B))
