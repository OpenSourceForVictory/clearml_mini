import pytest
from sqlalchemy import select
from backend.tests.test_execution import env,node
from backend.tests.test_orchestrations import setup,submit,healthy
from backend.app.core import Slot,Worker,ServiceInstance,EvaluationBatch,Job,Run,now
from backend.app.scheduling import tick


def additional_server(env):
    main,_,_=env
    token,ids=node(env,'server-extra')
    with main.SessionLocal() as s:
        source=s.scalar(select(Slot).join(Worker).where(Worker.node_name=='server'))
        slot=s.get(Slot,ids[0]);worker=s.get(Worker,slot.worker_id)
        for key in ('protocol_version','client_images','storage_report','storage_reported_at','runtime_report','runtime_reported_at'):
            setattr(slot,key,getattr(source,key))
        worker.roles=['server'];worker.max_concurrent=4
        worker.service_network={'address':'192.168.8.152','port_start':19000,'port_end':19002}
        s.commit()
    return token,ids[0]


def items(env,batch):
    with env[0].SessionLocal() as s:
        return list(s.scalars(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id==batch)))


@pytest.mark.parametrize('mode',['shared','exclusive'])
def test_target_two_with_one_client_and_idempotent_ticks(env,monkeypatch,mode):
    main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch);extra,es=additional_server(env)
    body['services'][0]['mode']=mode
    body['max_concurrent']=1;body['cases']=body['cases'][:1];body['services'][0]['instance_count']=2
    result=submit(env,body);identity=result['id']
    assert tick(main.SessionLocal)==3
    assert len(items(env,identity))==2
    assert len({i.slot_id for i in items(env,identity)})==2
    healthy(env,st,ss)
    # Client need not wait for the second service's readiness.
    tick(main.SessionLocal)
    from backend.tests.test_execution import claim
    assert claim(env,ct,cs[0]) is not None
    assert tick(main.SessionLocal)==0
    assert len(items(env,identity))==2
    data=c.get('/api/v1/orchestrations/'+identity,headers=a['alice']).json()
    assert data['orchestration']['definitions'][0]['instance_count']==2
    assert len(data['orchestration']['services'])==2


def test_partial_target_diagnosis_then_resource_recovery(env,monkeypatch):
    main,c,a=env;*_,body=setup(env,monkeypatch)
    body['max_concurrent']=1;body['services'][0]['instance_count']=2
    identity=submit(env,body)['id'];assert tick(main.SessionLocal)==2
    data=c.get('/api/v1/orchestrations/'+identity,headers=a['alice']).json()
    assert len(data['orchestration']['services'])==1
    assert data['orchestration']['scheduling'][0]['key']=='default'
    assert not any(r['code']=='batch_concurrency' for r in data['orchestration']['scheduling'][0]['reasons'])
    additional_server(env);assert tick(main.SessionLocal)==1
    assert len(items(env,identity))==2


@pytest.mark.parametrize('condition',['global','user','paused','finished','inactive'])
def test_target_obeys_budgets_and_lifecycle(env,monkeypatch,condition):
    main,_,_=env;*_,body=setup(env,monkeypatch);additional_server(env)
    body['services'][0]['instance_count']=2;body['max_concurrent']=1
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL','2')
    identity=submit(env,body)['id'];assert tick(main.SessionLocal)==2
    with main.SessionLocal() as s:
        batch=s.get(EvaluationBatch,identity)
        if condition=='paused':batch.scheduling_paused=True
        if condition=='finished':
            for job in s.scalars(select(Job).where(Job.batch_id==identity)):job.status='CANCELLED'
        if condition=='inactive':
            from backend.app.core import User
            s.get(User,batch.owner_id).active=False
        s.commit()
    if condition!='global':monkeypatch.setenv('MAX_ACTIVE_GLOBAL','8')
    if condition=='user':monkeypatch.setenv('MAX_ACTIVE_PER_USER','2')
    tick(main.SessionLocal)
    assert len(items(env,identity))==1


def test_stopped_instance_not_silently_replaced(env,monkeypatch):
    main,_,_=env;*_,body=setup(env,monkeypatch);additional_server(env)
    body['services'][0]['instance_count']=1
    identity=submit(env,body)['id'];tick(main.SessionLocal)
    with main.SessionLocal() as s:
        item=s.scalar(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id==identity));item.desired_running=False
        job=s.get(Job,item.current_job_id);job.status='FAILED';job.error='business failure'
        run=s.scalar(select(Run).where(Run.job_id==job.id));run.status='FAILED';run.cleanup_confirmed=True
        s.commit()
    tick(main.SessionLocal);assert len(items(env,identity))==1


@pytest.mark.parametrize('value',[0,21,-1,1.5])
def test_invalid_instance_count(env,monkeypatch,value):
    _,c,a=env;*_,body=setup(env,monkeypatch);body['services'][0]['instance_count']=value
    assert c.post('/api/v1/orchestrations/preflight',headers=a['alice'],json=body).status_code==400


def test_legacy_cap_is_not_a_target_and_mixed_fields_rejected(env,monkeypatch):
    main,c,a=env;st,ss,*_,body=setup(env,monkeypatch);additional_server(env)
    body['services'][0]['max_instances']=2;body['max_concurrent']=1
    identity=submit(env,body)['id'];assert tick(main.SessionLocal)==2
    healthy(env,st,ss);tick(main.SessionLocal);assert len(items(env,identity))==1
    body['services'][0]['instance_count']=2
    assert c.post('/api/v1/orchestrations/preflight',headers=a['alice'],json=body).status_code==400
