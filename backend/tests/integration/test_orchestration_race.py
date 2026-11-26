"""Independent schema on an explicitly supplied test PostgreSQL server."""
import os,uuid
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
import pytest
from sqlalchemy import select,text
from sqlalchemy.engine import make_url
from backend.tests.test_execution import env,node
from backend.tests.test_orchestrations import setup
from backend.app.core import Base,User,Pool,Run,ServiceInstance,EvaluationBatch,Job,Slot,now,session_factory
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
        st,ss,ct,cs,body=setup(env,monkeypatch)
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
        # A second server slot on the same physical node must reserve a new
        # port, even when multiple schedulers race for another batch.
        response=c.post('/api/v1/worker/register',headers=st,json={'pool_id':'mock','gpu_index':1,'gpu_uuid':'mock:server:1','gpu_model':'virtual','vram_gb':0})
        assert response.status_code==200,response.text
        with factory() as s:
            old=s.get(Slot,ss);slot=s.get(Slot,response.json()['slot_id'])
            for key in ('protocol_version','client_images','storage_report','storage_reported_at','runtime_report','runtime_reported_at'):
                setattr(slot,key,getattr(old,key))
            s.commit()
        assert c.post('/api/v1/orchestrations',headers={**a['alice'],'Idempotency-Key':'second'},json=body).status_code==201
        barrier=Barrier(8)
        with ThreadPoolExecutor(max_workers=8) as executor:results=list(executor.map(schedule,range(8)))
        assert sum(results)==2
        with factory() as s:
            instances=list(s.scalars(select(ServiceInstance)))
            assert len(instances)==2 and len({(i.worker_id,i.port) for i in instances})==2
    finally:
        factory.kw['bind'].dispose()
        with admin() as s:s.execute(text('DROP SCHEMA '+schema+' CASCADE'));s.commit()
        admin.kw['bind'].dispose()


def test_concurrent_large_managed_preflight_is_read_only(env,monkeypatch):
    """Large wizard requests share PostgreSQL capacity without reserving runs."""
    main,client,auth=env
    schema='preflight_'+uuid.uuid4().hex
    admin=session_factory(os.environ['TEST_DATABASE_URL'])
    with admin() as s:s.execute(text('CREATE SCHEMA '+schema));s.commit()
    url=make_url(os.environ['TEST_DATABASE_URL']).update_query_dict({'options':'-csearch_path='+schema})
    factory=session_factory(url.render_as_string(hide_password=False))
    try:
        Base.metadata.create_all(factory.kw['bind'])
        with main.SessionLocal() as old,factory() as s:
            for user in old.scalars(select(User)):
                s.add(User(id=user.id,username=user.username,role=user.role,password_hash=user.password_hash))
            s.add(Pool(id='mock',name='mock',gpu_model='virtual'));s.commit()
        monkeypatch.setattr(main,'SessionLocal',factory)
        *_,body=setup(env,monkeypatch)
        for index in range(24):
            node(env,f'extra-client-{index}',count=4)
        with factory() as s:
            for slot in s.scalars(select(Slot)):
                if slot.protocol_version:
                    continue
                slot.protocol_version=10
                slot.client_images=['image:1']
                slot.storage_report={'revision':0,'status':'applied'}
                slot.storage_reported_at=now()
                slot.runtime_report={'revision':0,'applied_revision':0,'images':{'image:1':{'ready':True}},'assets':{}}
                slot.runtime_reported_at=now()
            s.commit()
        body['cases']=[{'case_id':str(i),'task_name':f'scene-{i}','episodes':1} for i in range(100)]
        barrier=Barrier(8)

        def preflight(_):
            barrier.wait()
            return client.post('/api/v1/orchestrations/preflight',headers=auth['alice'],json=body)

        with ThreadPoolExecutor(max_workers=8) as executor:
            responses=list(executor.map(preflight,range(8)))
        assert all(response.status_code==200 for response in responses),[(r.status_code,r.text[:200]) for r in responses]
        assert all(len(response.json()['pair_checks'])==100 for response in responses)
        with factory() as s:
            assert not list(s.scalars(select(EvaluationBatch)))
            assert not list(s.scalars(select(Job)))
            assert not list(s.scalars(select(Run)))
            assert not list(s.scalars(select(ServiceInstance)))
    finally:
        factory.kw['bind'].dispose()
        with admin() as s:s.execute(text('DROP SCHEMA '+schema+' CASCADE'));s.commit()
        admin.kw['bind'].dispose()
