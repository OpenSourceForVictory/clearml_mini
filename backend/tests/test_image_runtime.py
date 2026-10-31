import copy
import json
import uuid
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
import pytest
from backend.tests.test_execution import env, node
from backend.tests.test_cs_protocol import submit_body
from backend.app.core import Slot, Worker as Machine, Run, now
from backend.app.scheduling import tick
from runner.image_runtime import validate, resolve
from worker.client import Worker
from worker.image_runtime import Runtime


def definition():
    return dict(name='user-image', version='1', image='custom:1', command=['python3','/app/test.py','${task_name}'],
                runtime=dict(environment={'TASK':'${task_name}'}, overridable_env=['LOG_LEVEL'], assets={'robotwin':'/data/robotwin'}))


def test_private_templates_and_snapshots(env):
    main,c,auth=env
    response=c.post('/api/v1/client-templates',headers=auth['alice'],json=definition())
    assert response.status_code==201,response.text
    template=response.json(); assert template['owner_id']=='alice'
    assert c.get('/api/v1/client-templates',headers=auth['bob']).json()==[]
    body=submit_body(template['id']);body['client']['environment']={'LOG_LEVEL':'${task_name}'}
    assert c.post('/api/v1/jobs',headers=auth['bob'],json=body).status_code==400
    r=c.post('/api/v1/jobs',headers=auth['alice'],json=body);assert r.status_code==201,r.text
    detail=c.get('/api/v1/jobs/'+r.json()['id'],headers=auth['alice']).json()
    assert detail['client_spec']['environment']=={'LOG_LEVEL':'${task_name}'}
    for override in ({'TASK':'override'},{'MINI_EVAL_INPUT':'x'},{'LOG_LEVEL':'${unknown}'}):
        body['client']['environment']=override
        assert c.post('/api/v1/jobs',headers=auth['alice'],json=body).status_code==400
    assert c.post('/api/v1/client-templates',headers=auth['bob'],json=definition()).status_code==409
    for runtime in ({'environment':{'PYTHONPATH':'/x'}},{'assets':{'x':'/etc'}},{'work_dir':'/data','assets':{'x':'/data/a'}}, {'assets':{'x':'/tmp/mini-eval-work'}}, {'environment':{'X':'${bad}'}}):
        d=definition();d['runtime']=runtime
        assert c.post('/api/v1/client-templates',headers=auth['alice'],json=d).status_code==400


def test_expansion_is_literal_and_bounded():
    cfg={'template':definition(), 'task_name':'$(touch /tmp/bad); echo x', 'episodes':2,'seed':7,'endpoint':'http://host:80', 'environment':{'LOG_LEVEL':'verbose'}}
    command,environment,cwd=resolve(cfg)
    assert command[-1]==cfg['task_name'] and environment['TASK']==cfg['task_name']
    assert environment['MINI_EVAL_EPISODES']=='2' and cwd=='/tmp/mini-eval-work'
    for path in ['/','/tmp','/proc/a','/opt/mini-eval','/tmp/mini-eval-runtime','/a/../b','/a,ro','/usr/lib','/tmp/mini-eval-output/a']:
        with pytest.raises(ValueError): validate({'assets':{'x':path}})


def runtime_setup(env):
    main,c,auth=env
    t=c.post('/api/v1/client-templates',headers=auth['alice'],json=definition()).json()['id']
    token,slots=node(env)
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0]);slot.protocol_version=5
        slot.storage_report={'revision':0,'status':'applied'};slot.storage_reported_at=now();s.commit()
        wid=slot.worker_id
    config={'images':['custom:1'],'assets':{'robotwin':'/data/assets'}}
    route=f'/api/v1/admin/nodes/{wid}/runtime'
    assert c.put(route,headers=auth['alice'],json={'expected_revision':0,'config':config}).status_code==403
    assert c.put(route,headers=auth['admin'],json={'expected_revision':0,'config':config}).status_code==200
    report={'revision':1,'applied_revision':1,'effective':config,'images':{'custom:1':{'ready':True}},'assets':{'robotwin':{'ready':True}}}
    return t,token,slots[0],route,report


def test_readiness_reservations_claim_drain_and_recovery(env):
    main,c,auth=env
    t,token,slot,route,report=runtime_setup(env)
    jid=c.post('/api/v1/jobs',headers=auth['alice'],json=submit_body(t)).json()['id']
    assert tick(main.SessionLocal)==0
    def heartbeat(r):return c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slot,'runtime':r})
    missing=copy.deepcopy(report);missing['assets']['robotwin']['ready']=False
    assert heartbeat(missing).status_code==200
    assert tick(main.SessionLocal)==0
    reasons=c.get(f'/api/v1/jobs/{jid}/runtime-readiness',headers=auth['alice']).json()
    assert 'robotwin' in reasons[0]['reasons'][0] and '/data' not in json.dumps(reasons)
    assert c.get(f'/api/v1/jobs/{jid}/runtime-readiness',headers=auth['bob']).status_code==404
    assert heartbeat(report).status_code==200
    assert tick(main.SessionLocal)==1
    # Configuration mutation releases unclaimed reservations and their credits.
    assert c.put(route,headers=auth['admin'],json={'expected_revision':1,'config':report['effective']}).status_code==200
    assert c.put(route,headers=auth['admin'],json={'expected_revision':1,'config':None}).status_code==409
    with main.SessionLocal() as s:
        r=s.query(Run).filter_by(job_id=jid).one();assert r.status=='PENDING' and r.slot_id is None and r.reserved_cpu==0
    report.update(revision=2,applied_revision=2)
    with main.SessionLocal() as s:
        s.get(Slot,slot).protocol_version=4;s.commit()
    assert tick(main.SessionLocal)==0
    assert heartbeat(report).status_code==409
    with main.SessionLocal() as s:
        s.get(Slot,slot).protocol_version=5;s.commit()
    assert heartbeat(report).status_code==200
    assert tick(main.SessionLocal)==1
    # Expired reports prevent claim, even for an already reserved task.
    with main.SessionLocal() as s:
        s.get(Slot,slot).runtime_reported_at=now()-timedelta(seconds=20);s.commit()
    claim=lambda:c.post(f'/api/v1/worker/slots/{slot}/claim',headers=token).json()['task']
    assert claim() is None
    heartbeat(report);task=claim();assert task['runtime_revision']==2
    c.put(route,headers=auth['admin'],json={'expected_revision':2,'config':report['effective']})
    ahead={**report,'revision':3,'applied_revision':3}
    assert heartbeat(ahead).status_code==409
    assert claim()['execution_id']==task['execution_id']  # recovery unaffected
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slot,'execution_id':task['execution_id']}).status_code==200


def test_runtime_persistence_busy_and_execution_snapshot(tmp_path):
    cfg={'workspace_root':str(tmp_path/'work'),'client_images':['old:1'],'runner_image':'probe:1'}
    parent=Worker(cfg,tmp_path/'state');parent.slot_id='slot'
    execution=str(uuid.uuid4());child=parent.child(execution)
    child.state={'task':{'execution_id':execution},'runtime':child.runtime_snapshot,'storage':child.storage_snapshot};child.save()
    desired={'revision':1,'config':{'images':['new:1'],'assets':{}}}
    def docker(*args,**kw):return SimpleNamespace(returncode=0,stderr='')
    parent.runtime.update(desired,True,parent.config,docker);parent.runtime.thread.join(2)
    assert parent.runtime.snapshot()['revision']==0
    parent.runtime.update(desired,False,parent.config,docker)
    if parent.runtime.thread:parent.runtime.thread.join(2)
    assert parent.runtime.snapshot()['revision']==1
    recovered=Worker(cfg,tmp_path/'state');recovered.recover_journals()
    assert recovered.runtime_snapshot['config']['images']==['new:1']
    assert recovered.children[execution].runtime_snapshot['config']['images']==['old:1']
    recovered.runtime.update({'revision':2,'config':None},False,recovered.config,docker)
    if recovered.runtime.thread: recovered.runtime.thread.join(2)
    assert recovered.runtime.snapshot()['config']['images']==['old:1']


def test_resources_are_independent_and_old_workers_cannot_run_new_templates():
    from backend.app.image_runtime import blockers
    machine=SimpleNamespace(runtime_revision=0)
    spec={'template':{'image':'custom:1','runtime':{'assets':{'needed':'/data/needed'}}}}
    slot=SimpleNamespace(protocol_version=3,runtime_report=None,runtime_reported_at=None)
    assert blockers(machine,slot,spec,now())==['需要 Worker v5']
    slot.protocol_version=5;slot.runtime_reported_at=now()
    slot.runtime_report={'revision':0,'applied_revision':0,'images':{'custom:1':{'ready':True},'other:1':{'ready':False}},
                         'assets':{'needed':{'ready':True},'unrelated':{'ready':False}}}
    assert blockers(machine,slot,spec,now())==[]
    slot.runtime_report['images']['custom:1']['ready']=False
    assert blockers(machine,slot,spec,now())==['所需镜像未批准或未就绪']


def test_runtime_probe_refresh_and_expiry(tmp_path):
    import time
    from worker.workspace import prepare
    cfg={'workspace_root':str(tmp_path/'workspace'),'client_images':['gone:1'],'runner_image':'probe:1'}
    prepare(cfg,cfg['workspace_root'])
    runtime=Runtime(cfg,tmp_path/'state')
    def missing(*args,**kwargs):raise RuntimeError('image missing')
    runtime.update({'revision':0,'config':None},False,cfg,missing);runtime.thread.join(2)
    assert not runtime.report()['images']['gone:1']['ready']
    runtime.probed_at=0
    def restored(*args,**kwargs):return SimpleNamespace(returncode=0,stderr='')
    runtime.update({'revision':0,'config':None},False,cfg,restored);runtime.thread.join(2)
    assert runtime.report()['images']['gone:1']['ready']
    runtime.probed_at=time.monotonic()-31
    assert runtime.report()['images']=={}
    runtime.close()
