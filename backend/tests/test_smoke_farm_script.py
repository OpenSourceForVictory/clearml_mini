"""Farm driver rejects false success and enforces its declared concurrency checks."""
import json
from pathlib import Path

import pytest
from scripts import smoke_farm as script


@pytest.mark.parametrize('fault',[None,'too-many','failed','retried','no-peak','timeout','cleanup'])
def test_farm_verdict_and_owned_cleanup(tmp_path,monkeypatch,fault):
    monkeypatch.setenv('ADMIN_USERNAME','fixture-admin');monkeypatch.setenv('ADMIN_PASSWORD','fixture-password')
    monkeypatch.setattr(script.smoke,'TOKEN',None)
    jobs=[];nodes=[];calls=[];processes=[];cleaned=[];polls=0
    def api(method,path,body=None):
        nonlocal polls
        calls.append((method,path,body))
        if path=='auth/login':return {'access_token':body['username']}
        if path=='admin/users':return {}
        if path=='admin/pools':return {'id':'pool'}
        if path=='jobs':
            assert script.smoke.TOKEN!='fixture-admin'
            job={'id':str(len(jobs)),'status':'PENDING','attempts':[{}]};jobs.append(job);return job
        if path=='admin/worker-credentials':
            node={'id':'node'+str(len(nodes)),'token':'worker-token','node_name':body['node_name']};nodes.append(node);return node
        if path=='admin/jobs?page_size=100':
            polls+=1
            rows=[{**j,'attempts':[{},{}] if fault=='retried' else [{}]} for j in jobs]
            if fault=='timeout':return rows
            for i,j in enumerate(rows):j['status']='RUNNING' if polls==1 and i<(3 if fault=='too-many' else 2) else 'SUCCEEDED'
            if fault=='failed':rows[0]['status']='FAILED'
            if fault=='no-peak':
                for j in rows:j['status']='SUCCEEDED'
            return rows
        if path.startswith('admin/nodes/'):
            assert body=={'enabled':False} and method=='PATCH';return {}
        raise AssertionError(path)
    monkeypatch.setattr(script.smoke,'api',api)
    ticks=iter(range(0,10000,10));monkeypatch.setattr(script.time,'monotonic',lambda:next(ticks));monkeypatch.setattr(script.time,'sleep',lambda _:None)
    class Process:
        def __init__(self,argv,**kwargs):
            path=Path(argv[-1]);cfg=json.loads(path.read_text())
            assert path.stat().st_mode & 0o777==0o600
            self.stopped=False;self.waited=False;processes.append(self)
            if fault=='cleanup':Path(str(path)+'.state').write_text(json.dumps({'task':{'execution_id':'execution'+str(len(processes))}}))
        def terminate(self):self.stopped=True
        def wait(self,timeout):self.waited=True
    monkeypatch.setattr(script.subprocess,'Popen',Process)
    class Worker:
        def __init__(self,config,path):self.state=json.loads(path.read_text())
        def cleanup(self,execution):cleaned.append(execution)
    monkeypatch.setattr('worker.client.Worker',Worker)
    if fault=='timeout':
        with pytest.raises(TimeoutError):script.main()
    elif fault not in (None,'cleanup'):
        with pytest.raises(AssertionError):script.main()
    else:script.main()
    assert len(jobs)==15 and len(nodes)==2 and len(processes)==3
    assert all(p.stopped and p.waited for p in processes)
    assert len([p for method,p,_ in calls if method=='PATCH'])==2
    assert len(cleaned)==(3 if fault=='cleanup' else 0)
