"""Inspect the exact container contract without invoking Docker or a GPU."""
import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from worker.client import Worker
from runner.cs_contract import VERSION


@pytest.fixture
def container(tmp_path,monkeypatch):
    worker=Worker({'runner_image':'runner:1','client_images':['client:1']},tmp_path/'state')
    worker.slot_id='slot';calls=[];inputs=[]
    def docker(*argv,**kwargs):
        calls.append(argv)
        if argv[0]=='cp' and Path(argv[1]).name=='input.json':inputs.append(json.loads(Path(argv[1]).read_text()))
        return SimpleNamespace(returncode=0,stdout='',stderr='')
    monkeypatch.setattr('worker.client.docker',docker)
    task={'execution_id':str(uuid.uuid4()),'job_id':'job','benchmark':'cs-client','image':'client:1',
          'gpu_uuid':None,'cpus':2,'ram_gb':4,'timeout_seconds':30,'episodes':2,
          'client_spec':{'contract_version':VERSION,'service_mode':'none',
                         'template':{'image':'client:1','command':['/usr/bin/true']}}}
    return worker,task,calls,inputs


@pytest.mark.parametrize('runtime',[False,True])
def test_client_mounts_resources_and_literal_input(container,runtime):
    worker,task,calls,inputs=container
    task['client_spec']['task_name']='literal; $(not-a-shell)'
    if runtime:
        task['client_spec']['template']['runtime']={'command_mode':'exec','assets':{},'working_directory':'/app/business'}
    worker.create(task)
    create=calls[0]
    assert create[0]=='create' and create[create.index('--network')+1]=='bridge'
    assert create[create.index('--cpus')+1]=='2' and create[create.index('--memory')+1]=='4g'
    assert create[create.index('--cap-drop')+1]=='ALL' and '--privileged' not in create
    mounts=[create[i+1] for i,v in enumerate(create) if v=='--mount']
    assert len(mounts)==4
    assert sum(v.endswith(',readonly') for v in mounts)==2
    assert inputs[0]['task_name']=='literal; $(not-a-shell)' and inputs[0]['execution_id']==task['execution_id']
    if runtime:
        assert create[create.index('--pull')+1]=='never'
        assert any(v[0]=='cp' and v[-1].endswith(':/tmp/') for v in calls)
    assert json.loads((worker.workspace/'runs'/task['execution_id']/'input.json').read_text())==inputs[0]


@pytest.mark.parametrize('cpus,ram_gb,missing',[ (0,0,('--cpus','--memory')), (0,4,('--cpus',)), (2,0,('--memory',)) ])
def test_unlimited_container_axes_omit_only_selected_limits(container,cpus,ram_gb,missing):
    worker,task,calls,_=container
    task.update(cpus=cpus,ram_gb=ram_gb)
    worker.create(task)
    create=calls[0]
    assert all(option not in create for option in missing)
    assert '--pids-limit' in create
    if cpus:assert create[create.index('--cpus')+1]==str(cpus)
    if ram_gb:assert create[create.index('--memory')+1]==str(ram_gb)+'g'


@pytest.mark.parametrize('mutation,match',[
    ({'storage_revision':1},'storage revision'),({'image':'unapproved'},'not approved'),
    ({'client_spec':{'contract_version':-1,'template':{'image':'client:1'}}},'contract')])
def test_invalid_task_never_creates_container(container,mutation,match):
    worker,task,calls,inputs=container;task.update(mutation)
    with pytest.raises((RuntimeError,ValueError),match=match):worker.create(task)
    assert calls==[]


def test_runtime_revision_and_missing_asset_fail_closed(container):
    worker,task,calls,inputs=container
    task['client_spec']['template']['runtime']={'assets':{'dataset':'/data/dataset'}}
    task['runtime_revision']=1
    with pytest.raises(RuntimeError,match='runtime revision'):worker.create(task)
    task['runtime_revision']=0
    with pytest.raises(RuntimeError,match='logical asset is not configured'):worker.create(task)
    assert not calls


@pytest.mark.parametrize('stderr,expected',[('No such container: test',None),('daemon offline','cannot inspect'),('', 'identity mismatch')])
def test_inspection_does_not_confuse_outage_or_foreign_container_with_absence(container,monkeypatch,stderr,expected):
    worker,task,calls,inputs=container
    monkeypatch.setattr('worker.client.docker',lambda *a,**kw:SimpleNamespace(returncode=1 if stderr else 0,stderr=stderr,
        stdout=json.dumps([{'Config':{'Labels':{'mini-eval.execution':'someone-else'}}} ])))
    if expected:
        with pytest.raises(RuntimeError,match=expected):worker.inspect(task['execution_id'])
    else:assert worker.inspect(task['execution_id']) is None

@pytest.mark.parametrize('runtime',[False,True])
def test_unlimited_time_bypasses_image_timer(container,runtime):
    worker,task,calls,inputs=container
    task['timeout_seconds']=0
    if runtime:
        task['client_spec']['template']['runtime']={'command_mode':'exec','assets':{},'working_directory':'/app/business'}
    worker.create(task)
    create=calls[0]
    assert 'runner.supervise' not in create and '--timeout' not in create
    assert '-m' in create and 'runner.cs_entry' in create
    assert inputs[0]['timeout_seconds']==0
