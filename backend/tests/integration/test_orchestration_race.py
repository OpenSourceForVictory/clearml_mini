"""Independent schema on an explicitly supplied test PostgreSQL server."""
import os,uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import select,text
from sqlalchemy.engine import make_url
from backend.tests.test_execution import env
from backend.tests.test_orchestrations import setup
from backend.app.core import Base,User,Pool,Run,ServiceInstance,EvaluationBatch,Job,session_factory
from backend.app.scheduling import tick

pytestmark=pytest.mark.skipif(not os.getenv('TEST_DATABASE_URL'),reason='dedicated PostgreSQL is required')


def test_joint_reservation_and_submission_race(env,monkeypatch):
    main,c,a=env
    schema='orchestration_'+uuid.uuid4().hex
    admin=session_factory(os.environ['TEST_DATABASE_URL'])
    with admin() as s:s.execute(text('CREATE SCHEMA '+schema));s.commit()
    url=make_url(os.environ['TEST_DATABASE_URL']).update_query_dict({'options':'-csearch_path='+schema})
    factory=session_factory(url.render_as_string(hide_password=False))
    try:
        Base.metadata.create_all(factory.kw['bind'])
        with main.SessionLocal() as old,factory() as s:
            for u in old.scalars(select(User)):s.add(User(id=u.id,username=u.username,role=u.role,password_hash=u.password_hash))
            s.add(Pool(id='mock',name='mock',gpu_model='virtual'));s.commit()
        monkeypatch.setattr(main,'SessionLocal',factory)
        *_,body=setup(env,monkeypatch)
        barrier=Barrier(4)
        def submit(_):
            barrier.wait();return c.post('/api/v1/orchestrations',headers={**a['alice'],'Idempotency-Key':'race'},json=body)
        with ThreadPoolExecutor(max_workers=4) as executor:responses=list(executor.map(submit,range(4)))
        assert all(r.status_code==201 for r in responses),[r.text for r in responses]
        assert len({r.json()['id'] for r in responses})==1
        barrier=Barrier(8)
        def schedule(_):barrier.wait();return tick(factory)
        with ThreadPoolExecutor(max_workers=8) as executor:results=list(executor.map(schedule,range(8)))
        assert sum(results)==2
        with factory() as s:
            assert len(list(s.scalars(select(EvaluationBatch))))==1
            assert len(list(s.scalars(select(ServiceInstance))))==1
            runs=list(s.scalars(select(Run).where(Run.status=='RESERVED')))
            assert len(runs)==2 and sum(r.waiting_service for r in runs)==1
            assert len({r.execution_id for r in runs})==2
            assert len(list(s.scalars(select(Job))))==4
    finally:
        factory.kw['bind'].dispose()
        with admin() as s:s.execute(text('DROP SCHEMA '+schema+' CASCADE'));s.commit()
        admin.kw['bind'].dispose()
