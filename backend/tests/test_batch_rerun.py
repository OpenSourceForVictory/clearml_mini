import copy
import json
import pytest
from sqlalchemy import select,func,event
from backend.tests.test_execution import env
from backend.tests.test_orchestrations import setup,submit
from backend.app.core import Job,EvaluationBatch,EvaluationSuite,EvaluationOrchestration,ClientTemplate,ServerTemplate,Pool,now


def draft(env,identity,user='alice'):
    return env[1].get('/api/v1/batches/'+identity+'/rerun-draft',headers=env[2][user])


def test_managed_roundtrip_edit_and_idempotency_readonly(env,monkeypatch):
    main,c,a=env;*_,body=setup(env,monkeypatch)
    body['services'][0]['instance_count']=2
    body['stop_after_batch']=False
    body['client']['timeout_seconds']=0
    for i,item in enumerate(body['cases']):
        item.update(episodes=i+1,seed=10+i,parameters={'zero':0,'false':False,'empty':'','different':i},resources={'cpus':i+1,'ram_gb':i+2,'cpu_unlimited':True,'memory_unlimited':False})
    b=submit(env,body)
    with main.SessionLocal() as s:
        before=[copy.deepcopy(j.client_spec) for j in s.scalars(select(Job).where(Job.batch_id==b['id']).order_by(Job.created_at,Job.id))]
    writes=[]
    def sql(conn,cursor,statement,parameters,context,many):
        if statement.lstrip().upper().startswith(('INSERT','UPDATE','DELETE')):writes.append(statement)
    engine=main.SessionLocal.kw['bind']
    event.listen(engine,'before_cursor_execute',sql)
    try:r=draft(env,b['id'])
    finally:event.remove(engine,'before_cursor_execute',sql)
    assert r.status_code==200,r.text
    assert not writes
    data=r.json();assert data['issues']==[]
    new=data['submission'];assert new['suite_id'] is None and new['client']['timeout_seconds']==0
    assert new['services'][0]['instance_count']==2 and new['stop_after_batch'] is False
    assert 'endpoint' not in new['client'] and 'endpoints' not in new['client']
    assert [x['parameters'] for x in new['cases']]==[x['parameters'] for x in body['cases']]
    assert new['cases'][0]['resources']['cpu_unlimited'] is True
    assert new['cases'][0]['resources']['memory_unlimited'] is False
    assert not {'idempotency_key','execution_id','claim','token','lease_expires_at'} & set(new)
    new['cases'][0]['parameters']['different']=99
    check=c.post('/api/v1/orchestrations/preflight',headers=a['alice'],json=new)
    assert check.status_code==200,check.text
    fresh=submit(env,new,'rerun');assert fresh['id']!=b['id']
    assert submit(env,new,'rerun')['id']==fresh['id']
    with main.SessionLocal() as s:
        after=[j.client_spec for j in s.scalars(select(Job).where(Job.batch_id==b['id']).order_by(Job.created_at,Job.id))]
        assert before==after


@pytest.mark.parametrize('mode',['external','none'])
def test_external_and_command_and_current_suite_do_not_replace_history(env,monkeypatch,mode):
    main,c,a=env;*_,body=setup(env,monkeypatch)
    body['services']=[];body['client']['service_mode']=mode
    if mode=='external':body['client']['endpoints']=[{'url':'http://192.168.0.2:8000','mode':'shared'}]
    else:
        t=c.post('/api/v1/admin/client-templates',headers=a['admin'],json={'name':'command','version':'1','image':'image:1','command':['true'],'task_type':'command'}).json()
        body['client']['template_id']=t['id']
    suite={'name':'original','version':'1','template_id':body['client']['template_id'],'cases':body.pop('cases')}
    body['new_suite']=suite
    b=submit(env,body)
    with main.SessionLocal() as s:
        source=s.get(EvaluationBatch,b['id']);s.get(EvaluationSuite,source.suite_id).cases=[];s.commit()
    result=draft(env,b['id']);assert result.status_code==200,result.text
    data=result.json();assert data['issues']==[] and len(data['submission']['cases'])==3
    assert data['submission']['client']['service_mode']==mode
    if mode=='external':assert data['submission']['client']['endpoints']==body['client']['endpoints']
    assert c.post('/api/v1/orchestrations/preflight',headers=a['alice'],json=data['submission']).status_code==200


def test_permission_deleted_empty_missing_and_heterogeneous(env,monkeypatch):
    main,c,a=env;*_,body=setup(env,monkeypatch);b=submit(env,body)
    assert draft(env,b['id'],'bob').status_code==404
    assert draft(env,b['id'],'admin').status_code==200
    assert draft(env,'missing').status_code==404
    with main.SessionLocal() as s:
        j=s.scalar(select(Job).where(Job.batch_id==b['id']));j.client_spec={**j.client_spec,'timeout_seconds':777};s.commit()
    assert any(i['code']=='heterogeneous' and i['fatal'] for i in draft(env,b['id']).json()['issues'])
    with main.SessionLocal() as s:
        j=s.scalar(select(Job).where(Job.batch_id==b['id']));j.client_spec={**j.client_spec,'disconnect_tolerance_seconds':[]};s.commit()
    assert any(i['code']=='snapshot_invalid' and i['fatal'] for i in draft(env,b['id']).json()['issues'])
    with main.SessionLocal() as s:
        j=s.scalar(select(Job).where(Job.batch_id==b['id']));j.client_spec=None;s.commit()
    assert draft(env,b['id']).status_code==409
    with main.SessionLocal() as s:
        s.get(EvaluationBatch,b['id']).deleted_at=now();empty=EvaluationBatch(owner_id=s.get(EvaluationBatch,b['id']).owner_id,title='empty');s.add(empty);s.commit();empty_id=empty.id
    assert draft(env,b['id']).status_code==404
    assert draft(env,empty_id).status_code==409


def test_legacy_count_template_changes_and_unavailable_pool(env,monkeypatch):
    main,c,a=env;*_,body=setup(env,monkeypatch);b=submit(env,body)
    data=draft(env,b['id']).json()
    assert any(i['code']=='instance_count_required' for i in data['issues'])
    assert data['submission']['services'][0]['instance_count'] is None
    with main.SessionLocal() as s:
        s.get(ClientTemplate,body['client']['template_id']).command=['changed']
        s.get(ServerTemplate,body['services'][0]['template_id']).enabled=False
        s.get(Pool,'mock').enabled=False
        s.commit()
    issues=draft(env,b['id']).json()['issues']
    assert {'template_changed','template_unavailable','pool_unavailable'}<= {i['code'] for i in issues}


def test_host_two_services_fixed_slot_and_missing_snapshot_fields(env,monkeypatch):
    main,c,a=env;_,slot,_,_,body=setup(env,monkeypatch)
    response=c.post('/api/v1/admin/client-templates',headers=a['admin'],json={'name':'host-rerun','version':'1','execution_mode':'host','command':['python','client.py'],'host_runtime':{'environment_name':'approved'},'protocols':['http']})
    assert response.status_code==201,response.text
    body['client']['template_id']=response.json()['id']
    body['services'][0].update(key='one',slot_id=slot,instance_count=2)
    body['services'].append({**body['services'][0],'key':'two','slot_id':None,'instance_count':1})
    b=submit(env,body)
    data=draft(env,b['id']).json();assert not data['issues']
    assert [x['key'] for x in data['submission']['services']]==['one','two']
    assert data['submission']['services'][0]['slot_id']==slot
    assert c.post('/api/v1/orchestrations/preflight',headers=a['alice'],json=data['submission']).status_code==200
    with main.SessionLocal() as s:
        j=s.scalar(select(Job).where(Job.batch_id==b['id']))
        spec=copy.deepcopy(j.client_spec);del spec['resources'];spec['token']='DO_NOT_EXPORT';spec['execution_id']='DO_NOT_EXPORT';j.client_spec=spec
        orch=s.get(EvaluationOrchestration,b['id']);definition=copy.deepcopy(orch.definition);del definition['services'][0]['template'];orch.definition=definition;s.commit()
    data=draft(env,b['id']).json()
    assert any(i['fatal'] for i in data['issues']) and 'DO_NOT_EXPORT' not in json.dumps(data)


def test_output_dir_is_reserved_in_typed_template_but_legacy_input_can_be_edited(env,monkeypatch):
    main,c,a=env;*_,body=setup(env,monkeypatch)
    typed=c.post('/api/v1/admin/client-templates',headers=a['admin'],json={
        'name':'typed-output','version':'1','image':'image:1','command':['python','client.py'],
        'parameter_schema':{'version':1,'fields':{'output_dir':{'type':'string'}}}})
    assert typed.status_code==400
    assert typed.json()['detail']=='invalid request parameters'

    body['cases'][0]['parameters']={'output_dir':'/business/old'}
    original=submit(env,body,'original-output')
    rerun=draft(env,original['id'])
    assert rerun.status_code==200,rerun.text
    submission=rerun.json()['submission']
    assert submission['cases'][0]['parameters']['output_dir']=='/business/old'
    submission['cases'][0]['parameters']['output_dir']='/business/new'
    check=c.post('/api/v1/orchestrations/preflight',headers=a['alice'],json=submission)
    assert check.status_code==200,check.text
    recreated=submit(env,submission,'edited-output')
    detail=c.get('/api/v1/batches/'+recreated['id'],headers=a['alice'])
    assert detail.status_code==200,detail.text
    job_id=next(case['job_id'] for case in detail.json()['cases'] if case['case_id']=='0')
    saved=c.get('/api/v1/jobs/'+job_id,headers=a['alice'])
    assert saved.status_code==200,saved.text
    assert saved.json()['client_spec']['parameters']['output_dir']=='/business/new'
    with main.SessionLocal() as s:
        first=s.scalar(select(Job).where(Job.batch_id==recreated['id'],Job.case_id=='0'))
        assert first.client_spec['parameters']['output_dir']=='/business/new'
        assert first.client_spec['template']['parameter_schema'] is None
        from runner.task_input import build
        resolved=build(first.client_spec,{'MINI_EVAL_OUTPUT_DIR':'/platform/assigned'})
        assert resolved['business']['output_dir']=='/business/new'
        assert resolved['platform']['paths']['output']=='/platform/assigned'


def test_rerun_rebinds_edited_business_output_option_from_new_job_snapshot(env,monkeypatch):
    from runner.parameter_contract import bind_values
    main,c,a=env;*_,body=setup(env,monkeypatch)
    response=c.post('/api/v1/admin/client-templates',headers=a['admin'],json={
        'name':'mapped-output','version':'1','image':'image:1','command':['python','client.py'],
        'parameter_schema':{'version':1,'fields':{'result_dir':{'type':'string',
            'binding':{'target':'argv','name':'--output-dir'}}}}})
    assert response.status_code==201,response.text
    body['client']['template_id']=response.json()['id']
    body['cases'][0]['parameters']={'result_dir':'/business/old'}
    old=submit(env,body,'mapped-original')
    submission=draft(env,old['id']).json()['submission']
    submission['cases'][0]['parameters']['result_dir']='/business/new'
    assert c.post('/api/v1/orchestrations/preflight',headers=a['alice'],json=submission).status_code==200
    new=submit(env,submission,'mapped-rerun')
    with main.SessionLocal() as s:
        original=s.scalar(select(Job).where(Job.batch_id==old['id'],Job.case_id=='0')).client_spec
        updated=s.scalar(select(Job).where(Job.batch_id==new['id'],Job.case_id=='0')).client_spec
        old_argv,_=bind_values(original['template']['parameter_schema'],original['parameters'],original['template']['command'])
        new_argv,_=bind_values(updated['template']['parameter_schema'],updated['parameters'],updated['template']['command'])
        assert old_argv==['python','client.py','--output-dir','/business/old']
        assert new_argv==['python','client.py','--output-dir','/business/new']


def test_rerun_draft_preserves_parameter_provenance_separately_from_submission(env,monkeypatch):
    _,c,a=env;*_,body=setup(env,monkeypatch)
    response=c.post('/api/v1/admin/client-templates',headers=a['admin'],json={
        'name':'typed-source','version':'1','image':'image:1','command':['python','client.py'],
        'parameter_schema':{'version':1,'fields':{'speed':{'type':'integer','default':3},
                                                'quality':{'type':'integer','default':0}}}})
    assert response.status_code==201,response.text
    body['client']['template_id']=response.json()['id']
    body['client']['parameters']={'quality':7}
    body['cases'][0]['parameters']={'speed':5}
    original=submit(env,body,'source-original')
    result=draft(env,original['id']);assert result.status_code==200,result.text
    data=result.json()
    assert data['parameter_sources']['0']['speed']=='case'
    assert data['parameter_sources']['1']['speed']=='template'
    assert data['parameter_sources']['0']['quality']=='batch'
    assert data['parameter_sources']['1']['quality']=='batch'
    assert data['submission']['cases'][1]['parameters']['speed']==3
    assert 'parameter_sources' not in data['submission']
