import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import jwt
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from backend.app.core import User, Pool, EvaluationBatch, Job, Run, session_factory
from backend.app.scheduling import transition
from backend.tests.test_execution import env

@pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'),reason='dedicated PostgreSQL required')
@pytest.mark.parametrize('quota',[1,10])
def test_bulk_single_and_replay(env,monkeypatch,quota):
    main,_,_=env
    factory=session_factory(os.environ['TEST_DATABASE_URL']);monkeypatch.setattr(main,'SessionLocal',factory)
    monkeypatch.setenv('MAX_BATCH_BACKLOG_PER_USER',str(quota))
    with factory() as s:
        u=User(username='retry-'+uuid.uuid4().hex,password_hash='unused');p=Pool(name='retry-'+uuid.uuid4().hex,gpu_model='virtual');s.add_all([u,p]);s.flush()
        b=EvaluationBatch(owner_id=u.id,title='race');s.add(b);s.flush();jobs=[];runs=[]
        for i in range(2):
            j=Job(owner_id=u.id,pool_id=p.id,batch_id=b.id,title='retry',benchmark='cs-client',model_ref='test',episodes=1,status='FAILED');s.add(j);s.flush()
            r=Run(job_id=j.id,attempt=1,status='FAILED',cleanup_confirmed=True);s.add(r);s.flush();jobs.append(j.id);runs.append(r.id)
        s.commit();uid,bid=u.id,b.id
    c=TestClient(main.app);auth={'Authorization':'Bearer '+jwt.encode({'sub':uid},main.SECRET,algorithm='HS256')}
    body={'items':[{'job_id':j,'expected_run_id':r} for j,r in zip(jobs,runs)]};path=f'/api/v1/batches/{bid}/retry-failed';barrier=Barrier(3)
    def post(path,body=None):barrier.wait();return c.post(path,headers=auth,json=body)
    with ThreadPoolExecutor(max_workers=3) as ex:
        futures=[ex.submit(post,path,body),ex.submit(post,path,body),ex.submit(post,f'/api/v1/jobs/{jobs[0]}/retry')]
        replies=[f.result() for f in futures]
    assert all(r.status_code in (200,202,409,429) for r in replies)
    with factory() as s:
        all_runs=s.scalars(select(Run).where(Run.job_id.in_(jobs))).all()
        assert sum(r.attempt==2 for r in all_runs)==min(quota,2)
        assert all(r.slot_id is None for r in all_runs)
        for r in all_runs:
            if r.attempt==2:transition(s,r,'FAILED')
        s.commit()
    # Replaying the stale selection cannot retry either successor even after it fails.
    replay=c.post(path,headers=auth,json=body).json()
    assert all(x['code']=='attempt_changed' for x in replay['items'] if x['job_id'] in {r.job_id for r in all_runs if r.attempt==2})
    with factory() as s:assert max(r.attempt for r in s.scalars(select(Run).where(Run.job_id.in_(jobs))))==2
