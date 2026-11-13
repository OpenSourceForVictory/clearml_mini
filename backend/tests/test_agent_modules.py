import json
import threading
import time
from types import SimpleNamespace
from pathlib import Path
import pytest
from node_agent.state import State
from node_agent.monitor import Monitor
from node_agent.programs import Programs
from node_agent.client import Agent


def test_state_lock_recovery_and_bounded_events(tmp_path):
    s=State(tmp_path/'control')
    with pytest.raises(BlockingIOError):State(tmp_path/'control')
    for _ in range(100):s.event('started')
    assert len(s.value['events'])==64
    s.acknowledge(90);s.close()
    recovered=State(tmp_path/'control')
    assert len(recovered.value['events'])==10 and recovered.value['next_event']==101
    assert (tmp_path/'control/state.json').stat().st_mode&0o777==0o600
    recovered.close()


def test_corrupt_state_and_symlink_rejected(tmp_path):
    p=tmp_path/'control';p.mkdir();(p/'state.json').write_text('{broken')
    with pytest.raises(ValueError):State(p)
    alias=tmp_path/'alias';alias.symlink_to(p)
    with pytest.raises(ValueError):State(alias)


def test_monitor_hung_probe_does_not_block(tmp_path):
    done=threading.Event()
    def slow():done.wait(3);return {}
    def bad():raise RuntimeError('secret')
    m=Monitor(tmp_path,{'host':lambda:{'cpu':4},'gpu':bad,'disk':slow})
    start=time.monotonic();m.sample(5);assert time.monotonic()-start<.2
    for _ in range(30):
        values=m.sample(5)
        if 'gpu' in values and 'host' in values:break
        time.sleep(.01)
    assert values['gpu']['status']=='failed' and values['host']['values']['cpu']==4
    m.started['disk']-=6
    assert m.sample(5)['disk']['status']=='failed'
    assert 'secret' not in json.dumps(values)
    done.set()


@pytest.mark.parametrize('unit',[{'scope':'user','name':'sshd.service'},{'scope':'root','name':'mini-eval-worker.service'},{'scope':'user','name':'mini-eval-worker;id.service'}])
def test_program_allowlist(unit):
    with pytest.raises(ValueError):Programs([unit])


def test_programs_idempotent_and_failed_command():
    state={'value':'inactive'};calls=[]
    def run(args,**kw):
        calls.append(args)
        if 'start' in args:state['value']='active'
        if 'stop' in args:state['value']='inactive'
        return SimpleNamespace(returncode=0,stdout=state['value'])
    p=Programs([{'scope':'user','name':'mini-eval-worker@gpu0.service'}],run=run)
    events=[]
    assert p.reconcile('running',events.append)
    assert p.reconcile('running',events.append)
    assert len([x for x in calls if 'start' in x])==1
    assert p.reconcile('stopped',events.append)
    assert events==['program_started','program_stopped']


class API:
    def __init__(self):self.calls=[];self.revision=0;self.target='running';self.busy=False;self.allow=True;self.upgrade=False
    def post(self,path,body):
        self.calls.append((path,body))
        if path=='register':return {'session':'a'*32}
        if path=='stop-permit':return {'allowed':self.allow}
        return {'revision':self.revision,'desired':{'heartbeat_seconds':2,'sample_seconds':5,'programs':self.target},'event_ack':0,'busy':self.busy,'upgrade_blocked':self.upgrade}


class FakePrograms:
    def __init__(self):self.targets=[]
    def inspect(self):return []
    def reconcile(self,target,event):self.targets.append(target);return True


def test_coordinator_busy_permit_upgrade_and_restart(tmp_path):
    api=API();programs=FakePrograms()
    config={'state_dir':str(tmp_path/'state')}
    agent=Agent(config,transport=api,programs=programs,monitor=Monitor(tmp_path,{'host':lambda:{}}))
    def step():agent.step();agent.controller.join(1)
    step();assert agent.status=='applied'
    api.revision=1;api.target='stopped';api.busy=True;step();assert programs.targets==['running']
    api.busy=False;api.allow=False;step();assert programs.targets==['running']
    api.allow=True;step();assert programs.targets[-1]=='stopped'
    api.upgrade=True;step();assert agent.status=='waiting'
    agent.state.close()
    recovered=Agent(config,transport=api,programs=programs,monitor=Monitor(tmp_path,{'host':lambda:{}}))
    assert recovered.state.value['revision']==1 and recovered.desired['programs']=='stopped'
    recovered.state.close()


def test_slow_program_inspection_does_not_block_heartbeat(tmp_path):
    gate=threading.Event()
    class Slow(FakePrograms):
        def inspect(self):gate.wait(2);return []
    api=API();agent=Agent({'state_dir':str(tmp_path/'state')},transport=api,programs=Slow(),monitor=Monitor(tmp_path,{'host':lambda:{}}))
    start=time.monotonic();agent.step();agent.step()
    assert time.monotonic()-start<.5
    assert len([x for x in api.calls if x[0]=='heartbeat'])==2
    gate.set();agent.controller.join(1);agent.state.close()


def test_state_on_network_filesystem_is_rejected(tmp_path,monkeypatch):
    original=Path.read_text
    def read(path,*a,**kw):
        if str(path)=='/proc/self/mountinfo':return '1 0 1:1 / / rw - ext4 /dev/root rw\n2 1 1:2 / '+str(tmp_path)+' rw - nfs4 server:/share rw\n'
        return original(path,*a,**kw)
    monkeypatch.setattr(Path,'read_text',read)
    with pytest.raises(ValueError,match='local filesystem'):State(tmp_path/'state')


def test_network_outage_preserves_state_and_recovers(tmp_path):
    api=API();programs=FakePrograms();agent=Agent({'state_dir':str(tmp_path/'state')},transport=api,programs=programs,monitor=Monitor(tmp_path,{'host':lambda:{}}))
    agent.step();agent.controller.join(1)
    pending=list(agent.state.value['events']);original=api.post
    def fail(*a,**kw):raise TimeoutError('unavailable')
    api.post=fail
    with pytest.raises(TimeoutError):agent.step()
    assert agent.state.value['events']==pending
    api.post=original;agent.step();agent.controller.join(1)
    assert agent.status=='applied';agent.state.close()


def test_transport_refuses_redirect_and_stale_identity():
    import urllib.error
    from node_agent.transport import Transport,Superseded
    transport=Transport('http://127.0.0.1:1','never-log-secret')
    class Opener:
        def __init__(self,code):self.code=code
        def open(self,*a,**kw):raise urllib.error.HTTPError('http://local',self.code,'sensitive',{},None)
    for code in (401,409):
        transport.opener=Opener(code)
        with pytest.raises(Superseded) as caught:transport.post('heartbeat',{})
        assert 'sensitive' not in str(caught.value)
    transport.opener=Opener(302)
    with pytest.raises(RuntimeError,match='request failed'):transport.post('heartbeat',{})
