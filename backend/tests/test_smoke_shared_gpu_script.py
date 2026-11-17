"""GPU acceptance driver's verdicts only: all GPU and Docker operations are fake."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import smoke_shared_gpu as script


@pytest.mark.parametrize('fault',[None,'device','restart','peer','capacity','failure','cleanup','render','timeout'])
def test_shared_gpu_driver_detects_regressions(monkeypatch,capsys,fault):
    monkeypatch.setenv('ADMIN_USERNAME','fixture');monkeypatch.setenv('ADMIN_PASSWORD','fixture-only')
    monkeypatch.setattr('worker.client.device',lambda cfg:{'gpu_uuid':'GPU-fixture'})
    data={'cancelled':False,'post_cancel_reads':0,'jobs':[],'processes':[],'docker_checks':0}
    ticks=iter(range(0,10000,10));monkeypatch.setattr(script.time,'monotonic',lambda:next(ticks));monkeypatch.setattr(script.time,'sleep',lambda _:None)
    def request(path,body=None,token=None,method=None):
        if path=='auth/login':return {'access_token':'fixture-token'}
        if path=='admin/pools':assert body['allow_shared'];return {'id':'pool'}
        if path=='admin/worker-credentials':data['node']=body['node_name'];return {'node_name':data['node'],'token':'worker-token'}
        if path=='workers':return [{'id':'slot','node_name':data['node'],'telemetry_fresh':True}]
        if path=='jobs':data['jobs'].append(body);return {'id':str(len(data['jobs'])-1)}
        if path.endswith('/cancel'):data['cancelled']=True;return {}
        if path.startswith('jobs/'):
            index=int(path.split('/')[1])
            if index==3:status='FAILED' if fault=='render' else 'SUCCEEDED'
            elif not data['cancelled']:status='RUNNING' if index<2 else 'PENDING'
            elif index==0:status='CANCELLED'
            elif index==1:
                data['post_cancel_reads']+=1
                status='RUNNING' if data['post_cancel_reads']==1 or fault=='timeout' else 'SUCCEEDED'
                if fault=='peer':status='CANCELLED'
                if fault=='failure' and data['post_cancel_reads']>1:status='FAILED'
            else:status='PENDING' if fault=='timeout' else 'SUCCEEDED'
            return {'status':status,'attempts':[{'cleanup_confirmed':fault!='cleanup'}]}
        raise AssertionError(path)
    monkeypatch.setattr(script,'request',request)
    class Process:
        def __init__(self,argv,**kwargs):
            path=Path(argv[-1]);assert path.stat().st_mode & 0o777==0o600
            self.stopped=False;self.waited=False;data['processes'].append(self)
        def kill(self):self.stopped=True
        def wait(self,timeout):self.waited=True
        def poll(self):return 0 if self.stopped else None
        def send_signal(self,sig):self.stopped=True
    monkeypatch.setattr(script.subprocess,'Popen',Process)
    def run(argv,**kwargs):
        if argv[1]=='exec':return SimpleNamespace(stdout='GPU-wrong' if fault=='device' else 'GPU-fixture')
        assert argv[:2]==['docker','ps'];data['docker_checks']+=1
        output='first a\nsecond b'
        if data['docker_checks']>1 and fault=='restart':output='first changed\nsecond b'
        if data['cancelled'] and fault=='capacity':output+='\nthird c'
        return SimpleNamespace(stdout=output)
    monkeypatch.setattr(script.subprocess,'run',run)
    if fault:
        with pytest.raises(AssertionError):script.main()
        assert '"status": "passed"' not in capsys.readouterr().out
    else:
        script.main();summary=json.loads(capsys.readouterr().out)
        assert summary['peak_concurrent']==2 and summary['restart_preserved_container_ids']
        assert len(summary['jobs'])==3 and summary['render_job']=='3'
    assert all(p.stopped and p.waited for p in data['processes'])
