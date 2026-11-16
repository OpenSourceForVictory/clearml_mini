"""Worker API transport, hardware parsing, and process lifecycle boundaries."""
import io
import json
import signal
import subprocess
import sys
import urllib.error
from types import SimpleNamespace

import pytest
from worker import client,accelerators,probe_dependencies


@pytest.mark.parametrize('mode',['success','http-error'])
def test_api_transport_bound_and_headers(monkeypatch,mode):
    def open_request(request,timeout):
        assert request.full_url=='http://test/api/v1/check' and timeout==5
        assert request.get_header('Authorization')=='Bearer test-only' and json.loads(request.data)=={'x':1}
        if mode=='http-error':raise urllib.error.HTTPError(request.full_url,409,'conflict',{},io.BytesIO(b'x'*3000))
        return io.BytesIO(b'{"ok":true}')
    monkeypatch.setattr(client.urllib.request,'urlopen',open_request)
    api=client.Client({'api_url':'http://test/','token':'test-only'})
    if mode=='success':assert api.post('check',{'x':1})=={'ok':True}
    else:
        with pytest.raises(client.APIError) as error:api.post('check',{'x':1})
        assert error.value.status==409 and len(str(error.value))<2100


@pytest.mark.parametrize('kind',['mock','gpu','npu'])
def test_hardware_identity_and_telemetry(monkeypatch,kind):
    cfg={'mock':kind=='mock','accelerator_type':kind,'gpu_index':2,'node_name':'test'}
    calls=[]
    def run(argv,**kwargs):
        calls.append(argv);assert argv[1]=='--id=2' and kwargs['check']
        return SimpleNamespace(stdout='GPU-2, Test GPU, 24576.5' if 'uuid,name,memory.total' in argv[2] else '12345.9, 42.7')
    monkeypatch.setattr(client.subprocess,'run',run)
    monkeypatch.setattr(accelerators,'ascend_sample',lambda config:{'identity':'NPU-2','model':'Ascend','total_mb':32768,'free_mb':8192,'utilization':25})
    hardware=client.device(cfg);telemetry=client.telemetry(cfg)
    assert hardware['gpu_index']==2
    if kind=='mock':assert hardware['gpu_uuid']=='mock:test:2' and telemetry['free_vram_mb']==0 and not calls
    elif kind=='npu':assert hardware['accelerator_type']=='npu' and hardware['vram_mb']==32768 and telemetry['gpu_utilization']==25 and not calls
    else:assert hardware['vram_mb']==24576 and telemetry=={'free_vram_mb':12345,'gpu_utilization':42} and len(calls)==2


@pytest.mark.parametrize('check,code',[(True,0),(False,1),(True,1)])
def test_docker_error_boundary(monkeypatch,check,code):
    def run(argv,**kwargs):
        assert argv==['docker','inspect','test'] and kwargs['timeout']==3 and kwargs['errors']=='replace'
        return SimpleNamespace(returncode=code,stderr='x'*2000)
    monkeypatch.setattr(client.subprocess,'run',run)
    if check and code:
        with pytest.raises(RuntimeError) as error:client.docker('inspect','test',timeout=3,check=check)
        assert len(str(error.value))<1100
    else:assert client.docker('inspect','test',timeout=3,check=check).returncode==code


@pytest.mark.parametrize('mode',['normal','signal','dependency-unavailable','run-fails'])
def test_worker_main_lock_and_shutdown(tmp_path,monkeypatch,mode):
    config=tmp_path/'worker.json';config.write_text('{"mock":true}')
    monkeypatch.setattr(sys,'argv',['worker','--config',str(config),'--once'])
    events=[];handlers={}
    def ensure():
        if mode=='dependency-unavailable':raise RuntimeError('probe dependency unavailable')
    monkeypatch.setattr(probe_dependencies,'ensure',ensure)
    monkeypatch.setattr(client.signal,'signal',lambda signum,handler:handlers.__setitem__(signum,handler))
    class Worker:
        def __init__(self,cfg,path):
            assert cfg['_config_path']==str(config) and cfg['mock']
            assert str(path).endswith('state/worker.json')
            events.append('created')
        def run(self,once):
            assert once;events.append('run')
            if mode=='signal':handlers[signal.SIGTERM](signal.SIGTERM,None)
            if mode=='run-fails':raise RuntimeError('run failed')
        def shutdown(self):events.append('shutdown')
    monkeypatch.setattr(client,'Worker',Worker)
    if mode=='run-fails':
        with pytest.raises(RuntimeError,match='run failed'):client.main()
    else:client.main()
    assert events==['created','run','shutdown']
