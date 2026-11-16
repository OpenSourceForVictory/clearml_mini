"""Launch contract checks with real temporary files; no privileged services/devices."""
import base64
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from backend.tests.test_host_backend import worker as host_worker
from backend.tests.test_worker_docker_contracts import container
from worker.host_backend import HostBackend


@pytest.mark.parametrize('mode', ['disk', 'vram', 'revision', 'missing-asset', 'control-symlink', 'not-executable', 'wrong-device'])
def test_host_rejects_invalid_launch_before_marker(tmp_path, monkeypatch, mode):
    (tmp_path/'app').write_text('business')
    w=host_worker(tmp_path,['python3','app']);task=w.state['task'];host=HostBackend(w);host.system=False
    monkeypatch.setattr('worker.host_backend.probe',lambda *a:None)
    expected={'disk':'insufficient workspace','vram':'insufficient free GPU','revision':'storage revision',
              'missing-asset':'unconfigured logical asset','control-symlink':'symlink',
              'not-executable':'not executable','wrong-device':'GPU task cannot'}[mode]
    if mode=='disk':monkeypatch.setattr('worker.host_backend.disk_status',lambda *a:{'free_mb':1,'min_free_mb':2})
    if mode=='vram':
        task.update(requested_vram_mb=100,vram_reserve_mb=20)
        monkeypatch.setattr('worker.client.telemetry',lambda *a:{'free_vram_mb':119})
    if mode=='revision':task['storage_revision']=1
    if mode=='missing-asset':task['client_spec']['template']['host_runtime']['assets']=['data']
    if mode=='control-symlink':host.root.parent.mkdir(parents=True,exist_ok=True);host.root.symlink_to(tmp_path)
    if mode=='not-executable':task['client_spec']['template']['command']=['app']
    if mode=='wrong-device':w.config['accelerator_type']='npu'
    with pytest.raises(RuntimeError,match=expected):host.create(task)
    assert not (host.root/'launch.json').exists()


@pytest.mark.parametrize('accelerator', ['gpu','npu','npu-no-pythonpath'])
@pytest.mark.parametrize('generic', [True,False])
def test_managed_identity_and_device_environment(tmp_path,monkeypatch,accelerator,generic):
    (tmp_path/'app.py').write_text('pass')
    w=host_worker(tmp_path,['python3','app.py','${assets:data}']);task=w.state['task']
    host=HostBackend(w);host.system=True
    account=SimpleNamespace(pw_uid=12345,pw_gid=12345,pw_dir='/home/business')
    monkeypatch.setattr('worker.host_backend.probe',lambda *a:None)
    monkeypatch.setattr('worker.host_identity.settings',lambda *a:account)
    owners=[];checks=[]
    monkeypatch.setattr('worker.host_backend.os.chown',lambda p,u,g:owners.append((Path(p),u,g)))
    monkeypatch.setattr('worker.host_identity.check_paths',lambda a,p:checks.append((a,p)))
    runtime=task['client_spec']['template']['host_runtime'];runtime['assets']=['data']
    if generic:runtime['command_mode']='exec'
    w.runtime_snapshot['config']['assets']['data']=w.config['assets_root']
    if accelerator.startswith('npu'):
        task['accelerator_type']='npu';w.config['accelerator_type']='npu'
        monkeypatch.setattr('worker.accelerators.assignment',lambda *a:None)
        env={'LD_LIBRARY_PATH':'/cann/lib'}
        if accelerator=='npu':env['PYTHONPATH']='/cann/python'
        monkeypatch.setattr('worker.npu_host.settings',lambda *a:(account,'/dev/davinci3',env))
    host.create(task)
    launch=json.loads((host.root/'launch.json').read_text())
    config=json.loads(Path(launch['environment']['MINI_EVAL_INPUT']).read_text())
    assert launch['environment']['HOME']=='/home/business'
    assert config['_resolved_command'][-1]==w.config['assets_root']
    assert owners and all(u==g==12345 for _,u,g in owners)
    assert checks[0][0] is account
    labels={label for label,_,_ in checks[0][1]}
    assert {'business cwd','asset data','work directory','output directory'}<=labels
    assert ('business script' in labels)==(not generic)
    if accelerator.startswith('npu'):
        assert launch['npu_device']=='/dev/davinci3'
        assert 'CUDA_VISIBLE_DEVICES' not in launch['environment']
        assert launch['environment']['ASCEND_RT_VISIBLE_DEVICES']=='0'
        assert launch['environment']['PYTHONPATH'].endswith(':/cann/python')==(accelerator=='npu')
    else:assert 'ASCEND_RT_VISIBLE_DEVICES' not in launch['environment']


@pytest.mark.parametrize('health_port', ['business',8081])
def test_container_service_ports_match_input(container,monkeypatch,health_port):
    w,task,calls,inputs=container
    monkeypatch.setattr('worker.client.check_bind',lambda *a:None)
    task['benchmark']='inference-service'
    task['client_spec'].update(workload='service',service={'address':'127.0.0.1','port':19000,
        'container_port':8080,'health_port':19001,'readiness':{'port':health_port}})
    w.create(task)
    args=calls[0];published=[args[i+1] for i,x in enumerate(args) if x=='--publish']
    assert published==['127.0.0.1:19000:8080']+([] if health_port=='business' else ['127.0.0.1:19001:8081'])
    assert inputs[0]['port']==8080 and inputs[0]['bind_host']=='0.0.0.0'
    assert inputs[0]['health_port']==(8080 if health_port=='business' else 8081)


@pytest.mark.parametrize('deadline_set',[True,False])
def test_artifact_upload_renews_before_each_file(container,monkeypatch,deadline_set):
    w,task,_,_=container;events=[]
    monkeypatch.setattr('worker.client.read_artifacts',lambda name:[('a.json',b'{}'),('b.bin',b'\x00\xff')])
    def post(path,body):
        events.append((path,body));return {'lease_seconds':30}
    w.api=SimpleNamespace(post=post)
    monkeypatch.setattr('worker.client.time.monotonic',lambda:100)
    deadline=[0] if deadline_set else None
    w.upload_artifacts(task,deadline)
    assert [p.rsplit('/',1)[-1] for p,_ in events]==['heartbeat','artifacts','heartbeat','artifacts']
    assert base64.b64decode(events[3][1]['content_base64'])==b'\x00\xff'
    if deadline_set:assert deadline==[128]


@pytest.mark.parametrize('stuck',[True,False])
def test_container_stop_requires_confirmed_exit(container,monkeypatch,stuck):
    w,task,calls,_=container;states=iter([True,stuck])
    monkeypatch.setattr(w,'inspect',lambda e:{'State':{'Running':next(states)}})
    if stuck:
        with pytest.raises(RuntimeError,match='has not stopped'):w.stop_container(task['execution_id'])
    else:w.stop_container(task['execution_id'])
    assert calls==[('kill',w.name(task['execution_id']))]
