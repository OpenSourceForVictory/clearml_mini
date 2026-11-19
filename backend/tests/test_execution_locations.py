import uuid
import pytest
from backend.tests.test_execution import env,node,submit,claim
from backend.app.core import ExecutionData,Run,Slot
from runner.process_log_contract import identity,cursor,parse_cursor,relative_directory,validate_policy,DEFAULT_POLICY,absolute_directory


def test_log_identity_and_cursor():
    a,b=str(uuid.uuid4()),str(uuid.uuid4())
    assert identity(a,'episode',1)!=identity(b,'episode',1)
    assert identity(a,'episode',1)!=identity(a,'episode',2)
    assert relative_directory('episode',2)=='episodes/000002'
    assert relative_directory('business')=='business'
    key=identity(a,'episode',1);page=cursor(key,b,12)
    assert parse_cursor(page,key,b)==12
    for args in [(page,identity(a,'episode',2),b),(page,key,a),('invalid',key,b)]:
        with pytest.raises(ValueError):parse_cursor(*args)
    for args in [(a,'episode',0),(a,'task',1),(a,'episode',True),(a,'other',0),('not-uuid','task',0)]:
        with pytest.raises(ValueError):identity(*args)
    assert validate_policy(DEFAULT_POLICY)==DEFAULT_POLICY
    for change in [dict(node_bytes=0),dict(scope_bytes=True),dict(segment_bytes=1),dict(scope_bytes=2**50)]:
        with pytest.raises(ValueError):validate_policy({**DEFAULT_POLICY,**change})
    with pytest.raises(ValueError):validate_policy({})
    for value in ['relative','/a/../b','//a','/a\x00b','/a/','/a/./b',None]:
        with pytest.raises(ValueError):absolute_directory(value)


def test_attempt_location_no_cleanup_filter_and_no_current_path_guess(env):
    main,c,auth=env;token,slots=node(env);job=submit(env);task=claim(env,token,slots[0])
    route=f'/api/v1/jobs/{job}/attempts/{task["run_id"]}/execution-location'
    assert c.get(route,headers=auth['alice']).json()['directories']==[]
    with main.SessionLocal() as s:
        for i,root in enumerate(['/old/runs','/second/runs','/bad/../runs']):
            s.add(ExecutionData(worker_id=s.get(Slot,slots[0]).worker_id,
                slot_id=slots[0],run_id=task['run_id'],execution_id=task['execution_id'],root_id=str(i),root_path=root,
                entry_name=task['execution_id'],generation=str(uuid.uuid4()),scan_status='complete',last_cleanup_status=None))
        s.commit()
    data=c.get(route,headers=auth['alice']).json()
    assert data['execution_id']==task['execution_id']!=data['run_id']
    assert {d['path'] for d in data['directories']}=={f'/old/runs/{task["execution_id"]}',f'/second/runs/{task["execution_id"]}',None}
    assert c.get(route,headers=auth['bob']).status_code==404
    assert c.get(route,headers=auth['admin']).status_code==200
    other=submit(env)
    assert c.get(route.replace(job,other),headers=auth['alice']).status_code==404
    detail=c.get('/api/v1/jobs/'+job,headers=auth['alice']).json()
    assert detail['attempts'][0]['execution_id']==task['execution_id']
    other_detail=c.get('/api/v1/jobs/'+other,headers=auth['alice']).json()
    pending=c.get(f'/api/v1/jobs/{other}/attempts/{other_detail["attempts"][0]["id"]}/execution-location',headers=auth['alice']).json()
    assert pending['execution_id'] is None and not pending['directories']


def test_reported_staging_path():
    from backend.app.execution_locations import reported_path
    execution=str(uuid.uuid4())
    assert reported_path('/old/runs',execution,execution,'staging')==f'/old/runs/.cleanup/{execution}'
    assert reported_path('/old/runs',execution,execution,'unexpected') is None


def test_location_ignores_foreign_slot_and_execution_inventory(env):
    main,c,auth=env;token,slots=node(env,count=2);job=submit(env);task=claim(env,token,slots[0])
    route=f'/api/v1/jobs/{job}/attempts/{task["run_id"]}/execution-location'
    with main.SessionLocal() as s:
        worker_id=s.get(Slot,slots[0]).worker_id
        for root,execution,slot_id in [('/correct/runs',task['execution_id'],slots[0]),('/foreign/runs',str(uuid.uuid4()),slots[0]),('/other-card/runs',task['execution_id'],slots[1])]:
            s.add(ExecutionData(worker_id=worker_id,slot_id=slot_id,run_id=task['run_id'],
                execution_id=execution,root_id=root,root_path=root,entry_name=execution,
                generation=str(uuid.uuid4()),scan_status='complete'))
        s.commit()
    response=c.get(route,headers=auth['alice'])
    assert response.status_code==200
    assert [d['path'] for d in response.json()['directories']]==['/correct/runs/'+task['execution_id']]
    assert c.get(route.replace(task['run_id'],str(uuid.uuid4())),headers=auth['alice']).status_code==404
