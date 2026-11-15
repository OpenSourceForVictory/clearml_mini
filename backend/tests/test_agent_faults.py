"""Failure contracts: durable acknowledgements and bounded recovery, no production access."""
import errno
import json
import threading
import time
from types import SimpleNamespace
import pytest
from node_agent.state import State
from node_agent.monitor import Monitor
from node_agent.programs import Programs
from node_agent.client import Agent
from backend.tests.test_agent_modules import API, FakePrograms


@pytest.mark.parametrize('code',[errno.ENOSPC,errno.EACCES,errno.EIO])
def test_config_write_failure_never_acknowledges_unpersisted_revision(tmp_path,monkeypatch,code):
    import node_agent.state as state_module
    api=API();programs=FakePrograms()
    agent=Agent({'state_dir':str(tmp_path/'state')},transport=api,programs=programs,monitor=Monitor(tmp_path,{'host':lambda:{}}))
    try:
        agent.step();agent.controller.join(1)
        disk=agent.state.path.read_bytes();revision=agent.state.value['revision']
        atomic=state_module.atomic_json
        def fail(*a,**kw):raise OSError(code,'do not expose raw filesystem error')
        monkeypatch.setattr(state_module,'atomic_json',fail)
        api.revision=revision+1
        # Fail in coordination after heartbeat succeeds, isolating config durability.
        reply=api.post('heartbeat',{})
        agent.coordinate(reply);assert agent.status=='failed'
        agent.coordinate(reply);assert agent.status=='failed'
        assert agent.state.value['revision']==revision
        assert agent.state.path.read_bytes()==disk
        monkeypatch.setattr(state_module,'atomic_json',atomic)
        agent.coordinate(reply);assert agent.status=='applied'
        assert json.loads(agent.state.path.read_text())['revision']==revision+1
    finally:agent.state.close()


def test_event_write_failure_preserves_sequence_and_pending_events(tmp_path,monkeypatch):
    import node_agent.state as mod
    state=State(tmp_path/'state');state.event('started');before=json.loads(json.dumps(state.value))
    def fail(*a):raise OSError(errno.ENOSPC,'full')
    monkeypatch.setattr(mod,'atomic_json',fail)
    try:
        with pytest.raises(OSError):state.event('config_applied')
        assert state.value==before
        with pytest.raises(OSError):state.acknowledge(1)
        assert state.value==before
    finally:state.close()


def test_hung_probe_has_one_thread_and_recovers(tmp_path):
    gate=threading.Event();calls=[]
    def probe():calls.append(1);gate.wait(3);return {'recovered':1}
    monitor=Monitor(tmp_path,{'gpu':probe})
    try:
        monitor.sample(5);thread=monitor.threads['gpu'];monitor.started['gpu']-=6
        for _ in range(100):
            assert monitor.sample(5)['gpu']['status']=='failed'
            assert monitor.threads['gpu'] is thread
        assert len(calls)==1
    finally:gate.set();thread.join(1)
    assert monitor.sample(120)['gpu']['status']=='ok'


@pytest.mark.parametrize('failure',['timeout','nonzero'])
def test_failed_start_is_rate_limited_then_recovers(failure,monkeypatch):
    import subprocess
    import node_agent.programs as module
    clock=[100.];calls=[];healthy=[False];state=['inactive']
    monkeypatch.setattr(module.time,'monotonic',lambda:clock[0])
    def run(args,**kw):
        if args[-2]=='start':
            calls.append(1)
            if not healthy[0]:
                if failure=='timeout':raise subprocess.TimeoutExpired(args,8)
                return SimpleNamespace(returncode=1,stdout='')
            state[0]='active'
        return SimpleNamespace(returncode=0,stdout=state[0])
    programs=Programs([{'scope':'system','name':'mini-eval-worker-test.service'}],run=run)
    events=[]
    assert not programs.reconcile('running',events.append)
    for _ in range(10):assert not programs.reconcile('running',events.append)
    assert len(calls)==1 and events==['program_failed']
    clock[0]+=31;healthy[0]=True
    assert programs.reconcile('running',events.append)
    assert len(calls)==2


@pytest.mark.parametrize('state',['unknown','activating','deactivating'])
def test_uncertain_program_state_never_sends_control(state):
    calls=[]
    def run(args,**kw):calls.append(args[-2]);return SimpleNamespace(returncode=0,stdout=state)
    p=Programs([{'scope':'system','name':'mini-eval-worker-test.service'}],run=run)
    for desired in ('running','stopped'):assert not p.reconcile(desired,lambda _:None)
    assert set(calls)=={'is-active'}


@pytest.mark.parametrize('content',['{}','{"revision":-1,"events":[],"next_event":1}','{"revision":0,"events":[],"next_event":0}'])
def test_invalid_state_fails_closed_and_releases_lock(tmp_path,content):
    root=tmp_path/'state';root.mkdir();p=root/'state.json';p.write_text(content)
    with pytest.raises(ValueError):State(root)
    assert p.read_text()==content
    p.write_text('{"revision":0,"events":[],"next_event":1}')
    state=State(root);state.close()

@pytest.mark.parametrize('payload',[b'not-json',b'x'*131073])
def test_invalid_or_oversized_server_response_cannot_change_state(payload):
    import io
    from node_agent.transport import Transport
    transport=Transport('http://localhost','private')
    class Opener:
        def open(self,request,timeout):
            assert timeout==5
            return io.BytesIO(payload)
    transport.opener=Opener()
    with pytest.raises(ValueError):transport.post('heartbeat',{})


def test_event_replay_and_ack_are_serialized(tmp_path):
    state=State(tmp_path/'state')
    def write():
        for _ in range(100):state.event('started')
    def ack():
        for _ in range(100):state.acknowledge(0)
    threads=[threading.Thread(target=f) for f in (write,write,ack)]
    for t in threads:t.start()
    for t in threads:t.join(5);assert not t.is_alive()
    assert state.value['next_event']==201
    assert len({x['sequence'] for x in state.value['events']})==64
    assert json.loads(state.path.read_text())==state.value
    state.close()


def test_expired_identity_stops_without_more_control(tmp_path):
    from node_agent.transport import Superseded
    class Dead(API):
        def post(self,*a,**kw):raise Superseded('invalid identity')
    programs=FakePrograms()
    agent=Agent({'state_dir':str(tmp_path/'state')},transport=Dead(),programs=programs,monitor=Monitor(tmp_path,{'host':lambda:{}}))
    agent.run()
    assert agent.stop.is_set() and not programs.targets
