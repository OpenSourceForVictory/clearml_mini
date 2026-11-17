"""Acceptance driver fault injection; API/process boundaries are simulated, not fleet acceptance."""
import io
import json
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from scripts import smoke_host_runtime, smoke_services


class Process:
    def __init__(self, argv, handles, stubborn=False):
        self.argv=argv;self.handles=handles;self.stubborn=stubborn
        self.terminated=False;self.killed=False;self.waited=False
    def terminate(self):self.terminated=True
    def kill(self):self.killed=True
    def wait(self,timeout=None):
        if self.stubborn and not self.killed:raise subprocess.TimeoutExpired(self.argv,timeout)
        self.waited=True;return 0


@pytest.fixture
def scenario(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('EVAL_API_URL','http://127.0.0.1:18090')
    monkeypatch.setenv('EVAL_ADMIN_PASSWORD','isolated-fixture')
    monkeypatch.setenv('STORAGE_TEST_HOST','127.0.0.1')
    data={'fault':None,'requests':[],'processes':[],'templates':[],'instances':{},'jobs':[], 'ticks':0}
    def clock():data['ticks']+=1;return data['ticks']
    monkeypatch.setattr('time.monotonic',clock)
    monkeypatch.setattr('time.sleep',lambda _:None)
    def popen(argv,stdout=None,stderr=None,**kwargs):
        if data['fault']=='spawn' and data['processes']:raise OSError('spawn failed')
        if '--config' in argv:
            path=Path(argv[argv.index('--config')+1]);cfg=json.loads(path.read_text())
            assert path.stat().st_mode & 0o777 == 0o600
            assert cfg['api_url']=='http://127.0.0.1:18090'
        process=Process(argv,[stdout,stderr],data['fault']=='stubborn');data['processes'].append(process);return process
    monkeypatch.setattr('subprocess.Popen',popen)
    def request(req,timeout):
        assert urlsplit(req.full_url).netloc=='127.0.0.1:18090'
        path=urlsplit(req.full_url).path.removeprefix('/api/v1/')
        body=json.loads(req.data) if req.data else None
        data['requests'].append((path,body,req.get_method()))
        if path!='auth/login':assert req.get_header('Authorization')=='Bearer fixture-token'
        if path=='auth/login':result={'access_token':'fixture-token'}
        elif path=='admin/pools':result={'id':'pool'}
        elif path=='admin/worker-credentials':result={'id':'node','node_name':body['node_name'],'token':'worker-token'};data['node']=result
        elif path.endswith('/host-environments'):
            result={} if body else {'slots':[{}, {'report':{'environments':{'test':{'ready':True}}}}]}
        elif path=='workers':
            result=[] if data['fault']=='timeout' else [dict(id=f'slot{i}',worker_id='node',node_name=data['node']['node_name'],gpu_index=i,online=True) for i in range(2)]
        elif path in ('admin/client-templates','admin/server-templates'):
            data['templates'].append(body);result={'id':f'template{len(data["templates"])}'}
        elif path=='admin/services' and body:
            identity=f'service{len(data["instances"])}';result={'id':identity,'endpoint':'http://127.0.0.1:19093','current_job_id':identity+'-first','state':'HEALTHY','revision':0}
            data['instances'][identity]=result.copy()
        elif path=='admin/services':
            result=list(data['instances'].values())
            if data['fault']=='health':result=[{**s,'state':'FAILED'} for s in result]
        elif path.startswith('admin/services/') and path.endswith('/control'):
            instance=data['instances'][path.split('/')[2]]
            assert body['expected_revision']==instance['revision']
            instance['revision']+=1;instance['state']='STOPPED' if body['action']=='stop' else 'HEALTHY'
            if body['action']=='start':instance['current_job_id']=instance['id']+'-second'
            result=instance.copy()
        elif path.startswith('admin/services/') and path.endswith('/logs'):
            result={'content':'bad-log' if data['fault']=='logs' else 'starting-service','history':[{},{}]}
        elif path=='jobs':
            data['jobs'].append(body);result={'id':f'job{len(data["jobs"])}'}
        elif path.startswith('jobs/'):
            if path.endswith('/episodes'):result={'progress':{'completed':1 if data['fault']=='episodes' else 2}}
            elif path.endswith('/artifacts'):result=[] if data['fault']=='artifacts' else [{'name':'report.json'}]
            elif path.endswith('/logs'):result={'chunks':[] if data['fault']=='logs' else [{}]}
            else:result={'id':path.split('/')[1],'status':'FAILED' if data['fault']=='job' else 'SUCCEEDED','attempts':[{'id':'attempt'}]}
        else:raise AssertionError('unexpected request '+path)
        return io.BytesIO(json.dumps(result).encode())
    monkeypatch.setattr('urllib.request.urlopen',request)
    return data


@pytest.mark.parametrize('module',[smoke_host_runtime,smoke_services])
@pytest.mark.parametrize('fault',[None,'job','logs','timeout','stubborn','spawn'])
def test_driver_verdict_and_cleanup(scenario,tmp_path,module,fault):
    scenario['fault']=fault
    if fault in ('job','logs'):
        with pytest.raises(AssertionError):module.main()
    elif fault=='timeout':
        with pytest.raises(RuntimeError,match='timed out'):module.main()
    elif fault=='spawn':
        with pytest.raises(OSError,match='spawn failed'):module.main()
    else:module.main()
    evidence=list(tmp_path.rglob('evidence.json'))
    if fault in (None,'stubborn'):
        assert len(evidence)==1
        rows=json.loads(evidence[0].read_text())
        assert len(rows)==(4 if module is smoke_host_runtime else 2)
        assert {r['mode'] for r in rows}=={'docker','host'}
        if module is smoke_host_runtime:assert {r['integration'] for r in rows}=={'sdk-command','direct'}
        else:
            controls=[body['action'] for path,body,_ in scenario['requests'] if path.endswith('/control')]
            assert controls[:6]==['stop','start','stop']*2
            assert all(v['state']=='STOPPED' for v in scenario['instances'].values())
    else:assert not evidence
    assert scenario['processes']
    for process in scenario['processes']:
        assert process.terminated and process.waited
        assert process.killed==(fault=='stubborn')
        assert all(handle.closed for handle in process.handles)


@pytest.mark.parametrize('fault',['episodes','artifacts'])
def test_host_driver_rejects_incomplete_results(scenario,tmp_path,fault):
    scenario['fault']=fault
    with pytest.raises(AssertionError):smoke_host_runtime.main()
    assert not list(tmp_path.rglob('evidence.json'))
    assert all(p.terminated and p.waited for p in scenario['processes'])


def test_service_driver_rejects_failed_health(scenario,tmp_path):
    scenario['fault']='health'
    with pytest.raises(RuntimeError,match='FAILED'):smoke_services.main()
    assert not list(tmp_path.rglob('evidence.json'))
    assert all(p.terminated and p.waited for p in scenario['processes'])


@pytest.mark.parametrize('module',[smoke_host_runtime,smoke_services])
def test_driver_refuses_other_api_before_mutation(scenario,module,monkeypatch):
    monkeypatch.setenv('EVAL_API_URL','http://192.0.2.1:18000')
    with pytest.raises(ValueError,match='isolated API'):module.main()
    assert not scenario['requests'] and not scenario['processes']
