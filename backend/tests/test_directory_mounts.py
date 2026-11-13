import copy
import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from runner.image_runtime import validate,resolve
from runner.mount_contract import requirements
from worker.mounts import resolve as resolve_mounts
from backend.tests.test_execution import env,node
from backend.tests.test_cs_protocol import submit_body
from backend.app.core import Slot,now
from backend.app.scheduling import tick

MOUNTS={'data':{'source':'directories:dataset','target':'/datasets','read_only':True,'required':True}}


@pytest.mark.parametrize('bad',[
    {'data':{'source':'/etc','target':'/data'}},
    {'data':{'source':'cache','target':'/data','read_only':False}},
    {'data':{'source':'assets:robotwin','target':'/data','read_only':False}},
    {'data':{'source':'directories:dataset','target':'/tmp/mini-eval-work/sub'}},
    {'data':{'source':'work','target':'/tmp/mini-eval-runtime'}},
    {'data':{'source':'work','target':'/data','read_only':'false'}},
    {'data':{'source':'work','target':'/data'},'other':{'source':'output','target':'/data/sub'}},
])
def test_mount_validation_rejects_unsafe_contract(bad):
    with pytest.raises(ValueError):validate({'mounts':bad})


def test_contract_is_immutable_and_literal():
    source=copy.deepcopy(MOUNTS);runtime=validate({'mounts':source})
    source['data']['target']='/changed';assert runtime['mounts']['data']['target']=='/datasets'
    cfg={'template':{'runtime':runtime,'command':['python3','app.py','${mounts:data}']},'task_name':'a','seed':0,'episodes':1,'endpoint':'http://host:80'}
    assert resolve(cfg)[0][-1]=='/datasets'
    assert 'mounts' not in validate({})  # Do not rewrite legacy snapshots.
    optional={'data':{**MOUNTS['data'],'required':False}}
    assert requirements(optional,{}, {})==[]
    assert requirements(optional,{'directories':{'dataset':{'path':'/private/path'}}},{})==['挂载目录未就绪: data']


def test_api_gates_old_workers_readiness_permissions_and_revisions(env):
    main,c,a=env;token,slots=node(env);slot=slots[0]
    t=c.post('/api/v1/admin/client-templates',headers=a['admin'],json={'name':'mounted','version':'1','image':'custom:1','command':['python3','business.py','${mounts:data}'],'runtime':{'mounts':MOUNTS}})
    assert t.status_code==201,t.text
    job=c.post('/api/v1/jobs',headers=a['alice'],json=submit_body(t.json()['id']));assert job.status_code==201,job.text
    with main.SessionLocal() as s:
        item=s.get(Slot,slot);item.protocol_version=8;wid=item.worker_id
        item.storage_report={'revision':0,'status':'applied'};item.storage_reported_at=now();s.commit()
    route='/api/v1/admin/nodes/'+wid+'/runtime'
    config={'images':['custom:1'],'assets':{},'directories':{'dataset':{'path':'/data/node-a','writable':False}}}
    assert c.put(route,headers=a['alice'],json={'expected_revision':0,'config':config}).status_code==403
    assert c.put(route,headers=a['admin'],json={'expected_revision':0,'config':config}).status_code==200
    report={'revision':1,'applied_revision':1,'effective':config,'images':{'custom:1':{'ready':True}},'directories':{'dataset':{'ready':True}}}
    def heartbeat(r):return c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slot,'runtime':r})
    assert heartbeat(report).status_code==200
    assert tick(main.SessionLocal)==0
    with main.SessionLocal() as s:s.get(Slot,slot).protocol_version=9;s.commit()
    missing=copy.deepcopy(report);missing['directories']['dataset']['ready']=False
    assert heartbeat(missing).status_code==200 and tick(main.SessionLocal)==0
    response=c.get('/api/v1/jobs/'+job.json()['id']+'/runtime-readiness',headers=a['alice'])
    assert '挂载目录未就绪' in response.text and '/data/node-a' not in response.text
    assert heartbeat(report).status_code==200 and tick(main.SessionLocal)==1
    assert c.put(route,headers=a['admin'],json={'expected_revision':1,'config':config}).status_code==200
    assert tick(main.SessionLocal)==0  # Old readiness cannot admit the new revision.
    report.update(revision=2,applied_revision=2);assert heartbeat(report).status_code==200
    assert tick(main.SessionLocal)==1
    task=c.post('/api/v1/worker/slots/'+slot+'/claim',headers=token).json()['task']
    assert task['runtime_revision']==2 and task['client_spec']['template']['runtime']['mounts']==MOUNTS


def test_different_nodes_resolve_same_template_and_deny_unapproved_writes(tmp_path):
    from worker.workspace import prepare,execution_dirs
    for name in ('node-a','node-b'):
        root=tmp_path/name;root.mkdir();work=prepare({'workspace_root':str(root/'work')},str(root/'work'))
        extra=root/'data';extra.mkdir();config={'workspace_root':str(work),'assets_root':str(work/'assets'),'cache_root':str(work/'cache'),'min_free_disk_gb':0}
        mapped={'directories':{'dataset':{'path':str(extra),'writable':False}}};directory=execution_dirs(work,str(uuid.uuid4()))
        assert resolve_mounts(MOUNTS,mapped,config,root/'state',directory)==[(str(extra),'/datasets',True)]
        rw={'data':{**MOUNTS['data'],'read_only':False}}
        with pytest.raises(ValueError,match='write not approved'):resolve_mounts(rw,mapped,config,root/'state',directory)
        mapped['directories']['dataset']['writable']=True
        assert resolve_mounts(rw,mapped,config,root/'state',directory)[0][-1] is False
        extra.rmdir();extra.symlink_to(work/'assets',target_is_directory=True)
        with pytest.raises(ValueError,match='not ready'):resolve_mounts(MOUNTS,mapped,config,root/'state',directory)


@pytest.mark.skipif(os.getenv('DIRECTORY_MOUNT_TEST')!='1',reason='requires local Docker image')
@pytest.mark.parametrize('node_name',['node-a','node-b'])
def test_real_docker_mounts_resolve_node_paths_and_enforce_read_only(tmp_path,node_name):
    from backend.tests.test_service_recovery import service
    from worker.client import docker
    data=tmp_path/node_name;data.mkdir();(data/'input.txt').write_text(node_name)
    writable=tmp_path/'shared';writable.mkdir();writable.chmod(0o777)
    program="""from pathlib import Path
import os
assert Path('/datasets/input.txt').read_text()==os.environ['EXPECTED_NODE']
try:Path('/datasets/forbidden').write_text('bad')
except OSError:pass
else:raise AssertionError('read-only mount is writable')
Path('/shared/result.txt').write_text(os.environ['EXPECTED_NODE'])
Path('/work-alias/local.txt').write_text('isolated')
print('mount-check-passed',flush=True)
"""
    build,task=service(tmp_path,program,'docker');w=build()
    task['client_spec']['template']['runtime']={'mounts':{**MOUNTS,
        'shared':{'source':'directories:shared','target':'/shared','read_only':False},
        'scratch':{'source':'work','target':'/work-alias'}},'environment':{'EXPECTED_NODE':node_name}}
    w.runtime_snapshot['config']['directories']={'dataset':{'path':str(data),'writable':False},'shared':{'path':str(writable),'writable':True}}
    w.state={'task':task}
    try:
        w.create(task);docker('start',w.name(task['execution_id']))
        result=docker('wait',w.name(task['execution_id']),timeout=30)
        captured=docker('logs',w.name(task['execution_id']));logs=captured.stdout+captured.stderr
        assert result.stdout.strip()=='0',logs
        assert 'mount-check-passed' in logs
        assert (writable/'result.txt').read_text()==node_name and not (data/'forbidden').exists()
        assert (w.workspace/'runs'/task['execution_id']/'work'/'local.txt').read_text()=='isolated'
    finally:w.remove(task['execution_id'])


def test_directory_probe_reports_resources_independently(tmp_path):
    from worker.image_runtime import Runtime
    from worker.workspace import prepare
    work=prepare({'workspace_root':str(tmp_path/'work')},str(tmp_path/'work'))
    extra=tmp_path/'shared';extra.mkdir()
    config={'workspace_root':str(work),'execution_modes':['host'],'min_free_disk_gb':0}
    runtime=Runtime(config,tmp_path/'state')
    desired={'revision':1,'config':{'images':[],'assets':{},'directories':{'good':{'path':str(extra),'writable':True},'missing':{'path':str(tmp_path/'missing'),'writable':False}}}}
    try:
        runtime.update(desired,False,config,lambda *a,**k:None);runtime.thread.join(5)
        report=runtime.report();assert report['directories']['good']['ready']
        assert not report['directories']['missing']['ready']
        assert str(tmp_path) not in report['directories']['missing']['error']
    finally:runtime.close()


def test_command_sdk_expands_mounts_for_each_business_process(tmp_path,monkeypatch):
    import sys
    from runner.command_sdk import CommandClient
    data=tmp_path/'data';data.mkdir();(data/'scene.txt').write_text('scene')
    script=tmp_path/'business.py';script.write_text("import json,os,sys\nfrom pathlib import Path\nassert Path(sys.argv[1],'scene.txt').read_text()=='scene'\nassert os.environ['DATA']==sys.argv[1]\nPath(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({'success':True}))\n")
    runtime=validate({'mounts':{'data':{'source':'directories:dataset','target':str(data)}},'environment':{'DATA':'${mounts:data}'}})
    task={'task_name':'test','episodes':2,'seed':0,'endpoint':'http://example:80','template':{'runtime':runtime,'command':[sys.executable,str(script),'${mounts:data}']}}
    monkeypatch.setenv('MINI_EVAL_INPUT',str(tmp_path/'input.json'))
    context=SimpleNamespace(work_dir=tmp_path,output_dir=tmp_path)
    client=CommandClient()
    for i in range(2):assert client.run_episode(task,SimpleNamespace(id=i,seed=i),context).success


def test_server_mount_override_is_checked_before_deployment(env):
    from backend.tests.test_services import setup
    _,c,a=env;_,_,body=setup(env)
    r=c.post('/api/v1/admin/server-templates',headers=a['admin'],json={'name':'mount-server','version':'1','image':'image:1','command':['python3','service.py'],'runtime':{'mounts':MOUNTS,'overridable_env':['DATA']}})
    assert r.status_code==201,r.text
    body.update(template_id=r.json()['id'],environment={'DATA':'${mounts:missing}'})
    r=c.post('/api/v1/admin/services',headers=a['admin'],json=body)
    assert r.status_code==400,r.text
    assert c.get('/api/v1/admin/services',headers=a['admin']).json()==[]
    body['environment']={'DATA':'${mounts:data}'}
    r=c.post('/api/v1/admin/services',headers=a['admin'],json=body)
    assert r.status_code==201,r.text
