"""Control writes must not depend on ordinary capacity or post-commit reads."""
import pytest
from sqlalchemy import event, select
from backend.tests.test_execution import env
from backend.tests.test_monitoring import batch, fail, selection
from backend.tests.test_services import setup as service_setup
from backend.app import db_budget
from backend.app.core import Job, Run


@pytest.mark.parametrize('path', ['/api/v1/jobs/j/retry', '/api/v1/batches/b/retry-failed'])
def test_retry_uses_control_capacity(path):
    assert db_budget.request_category(path) == 'control'
    assert db_budget.request_category(path, 'GET') == 'ordinary'
    assert db_budget.request_category(path + '/extra') == 'ordinary'


def forbid_post_commit_reads(engine):
    committed = []
    def commit(connection):
        committed.append(True)
    def query(*args):
        if committed:
            raise db_budget.DatabaseBusy('post-commit read exhausted request budget')
    event.listen(engine, 'commit', commit)
    event.listen(engine, 'before_cursor_execute', query)
    return lambda: (event.remove(engine, 'commit', commit), event.remove(engine, 'before_cursor_execute', query))


def test_single_retry_does_not_read_after_commit(env):
    main,c,a=env;b,_=batch(env);fail(env,b)
    path='/api/v1/jobs/'+b['cases'][0]['job_id']+'/retry'
    remove=forbid_post_commit_reads(main.SessionLocal.kw['bind'])
    try:
        response=c.post(path,headers=a['alice'])
        assert response.status_code==202,response.text
        assert response.json()['attempt']==2
    finally:remove()
    assert c.post(path,headers=a['alice']).status_code==409


def test_service_control_ack_survives_unavailable_details_and_replay(env,monkeypatch):
    from backend.app import services
    main,c,a=env;_,_,body=service_setup(env)
    item=c.post('/api/v1/admin/services',headers=a['admin'],json=body).json()
    path='/api/v1/admin/services/'+item['id']+'/control'
    def unavailable(*args,**kwargs):raise db_budget.DatabaseBusy('details overloaded')
    monkeypatch.setattr(services,'view',unavailable)
    for revision,action in enumerate(['stop','start']):
        remove=forbid_post_commit_reads(main.SessionLocal.kw['bind'])
        try:
            r=c.post(path,headers=a['admin'],json={'action':action,'expected_revision':revision})
            assert r.status_code==200,r.text
            assert r.json()['accepted'] is True
            assert r.json()['revision']==revision+1
        finally:remove()
        assert c.post(path,headers=a['admin'],json={'action':action,'expected_revision':revision}).status_code==409


def test_bulk_retry_only_loads_selected_jobs(env):
    from backend.tests.test_preflight import setup
    main,c,a=env;body,_=setup(env)
    body['cases']=[{**body['cases'][0],'case_id':str(i)} for i in range(100)]
    response=c.post('/api/v1/batches',headers=a['alice'],json=body)
    assert response.status_code==201,response.text
    b=response.json();fail(env,b)
    loaded=[]
    def load(session,obj):
        if isinstance(obj,Job):loaded.append(obj.id)
    event.listen(main.SessionLocal,'loaded_as_persistent',load)
    try:
        selected=selection(b)['items'][:1]
        r=c.post('/api/v1/batches/'+b['id']+'/retry-failed',headers=a['alice'],json={'items':selected})
        assert r.status_code==200,r.text
        assert r.json()['retried']==1
        assert set(loaded)=={selected[0]['job_id']}
    finally:event.remove(main.SessionLocal,'loaded_as_persistent',load)


def test_orchestrated_control_ack_without_detail_queries(env,monkeypatch):
    from backend.tests.test_orchestrations import setup,submit
    from backend.app import orchestrations
    from backend.app.core import ServiceInstance
    main,c,a=env;*_,body=setup(env,monkeypatch)
    b=submit(env,body)
    from backend.app.scheduling import tick
    tick(main.SessionLocal)
    with main.SessionLocal() as s:
        item=s.scalar(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id==b['id']))
        identity=item.id
    path=f"/api/v1/orchestrations/{b['id']}/services/{identity}/control"
    def unavailable(*args,**kwargs):raise db_budget.DatabaseBusy('details overloaded')
    monkeypatch.setattr(orchestrations,'view',unavailable)
    for revision,action in enumerate(['stop','start']):
        remove=forbid_post_commit_reads(main.SessionLocal.kw['bind'])
        try:
            r=c.post(path,headers=a['alice'],json={'action':action,'expected_revision':revision})
            assert r.status_code==200,r.text
            assert r.json()['id']==identity and r.json()['accepted']
        finally:remove()
        assert c.post(path,headers=a['alice'],json={'action':action,'expected_revision':revision}).status_code==409


def test_service_commit_failure_does_not_ack_or_persist(env,monkeypatch):
    from backend.app.core import ServiceInstance
    main,c,a=env;_,_,body=service_setup(env)
    item=c.post('/api/v1/admin/services',headers=a['admin'],json=body).json()
    def fail_commit(session):raise db_budget.DatabaseBusy('commit unavailable')
    event.listen(main.SessionLocal,'before_commit',fail_commit)
    try:
        with pytest.raises(db_budget.DatabaseBusy,match='commit unavailable'):
            c.post('/api/v1/admin/services/'+item['id']+'/control',headers=a['admin'],
                   json={'action':'stop','expected_revision':0})
    finally:event.remove(main.SessionLocal,'before_commit',fail_commit)
    with main.SessionLocal() as s:
        assert s.get(ServiceInstance,item['id']).revision==0
        assert s.get(Job,item['current_job_id']).status=='PENDING'
