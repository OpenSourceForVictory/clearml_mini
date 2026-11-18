"""Startup renewal uses real threads; Docker/systemd/API are controlled boundaries."""
import subprocess
import threading
import time
import urllib.error
from types import SimpleNamespace

import pytest
from backend.tests.test_worker_execution_recovery import execution
from worker import client


def setup(execution, monkeypatch, *, host=False, lease=3):
    worker, task, events = execution
    task.update(lease_seconds=lease, client_spec={'template':{
        'task_type':'command', 'execution_mode':'host' if host else 'docker'}})
    state = {'created':False, 'started':False}
    original = worker.api.post
    def post(path, body=None):
        return {**original(path, body), 'lease_seconds':lease}
    worker.api.post = post
    def inspect(identity):
        if not state['created']:return None
        return {'State':{'Status':'exited' if state['started'] else 'created',
                         'Running':False, 'ExitCode':0}}
    monkeypatch.setattr(worker, 'inspect', inspect)
    def start(*args, **kwargs):
        state['started']=True
        events.append(('start', None))
        return SimpleNamespace(stdout='', stderr='')
    monkeypatch.setattr(client, 'docker', lambda *args,**kw: start() if args[0]=='start' else SimpleNamespace(stdout='',stderr=''))
    if host:
        monkeypatch.setattr(client.HostBackend,'start',start)
        monkeypatch.setattr(client.HostBackend,'logs',lambda self:('', ''))
    return worker, task, events, state


@pytest.mark.parametrize('host',[False, True])
def test_slow_real_shell_preparation_renews_without_starting_early(execution,monkeypatch,host):
    worker,task,events,state=setup(execution,monkeypatch,host=host)
    original=worker.api.post
    beats=[]
    def post(path,body=None):
        if path=='worker/heartbeat' and body.get('started') is False:
            beats.append(time.monotonic())
            assert not state['started']
        return original(path,body)
    worker.api.post=post
    def create(task):
        # Longer than the local one-second budget of the initial 3-second lease.
        subprocess.run(['/bin/sh','-c','sleep 1.4'],check=True,timeout=5)
        state['created']=True
    monkeypatch.setattr(worker,'create',create)
    worker.execute(task)
    assert len(beats)>=3
    assert len([p for p,b in events if p=='start'])==1
    assert next(b for p,b in events if p.endswith('/complete'))['status']=='SUCCEEDED'
    assert not any(t.name=='startup-lease-'+task['execution_id'] for t in threading.enumerate())
    assert worker.state is None


@pytest.mark.parametrize('fault',['cancel','stop','denied','reclaimed','late','outage','busy','create-error'])
def test_startup_abort_never_launches_after_preparation(execution,monkeypatch,fault):
    worker,task,events,state=setup(execution,monkeypatch)
    observed=threading.Event()
    original=worker.api.post
    beats=0
    def post(path,body=None):
        nonlocal beats
        if path=='worker/heartbeat' and body.get('started') is False:
            beats+=1
            if beats>1:
                observed.set()
                if fault=='denied':raise client.APIError(401,'denied')
                if fault=='reclaimed':raise client.APIError(409,'reclaimed')
                if fault=='outage':raise TimeoutError('offline')
                if fault=='busy':raise client.APIError(503,'busy')
                if fault=='late':time.sleep(1.2)
                if fault=='cancel':return {**original(path,body),'cancel':True}
        return original(path,body)
    worker.api.post=post
    def create(task):
        assert observed.wait(3)
        if fault=='stop':worker.stop_event.set()
        if fault=='create-error':raise RuntimeError('preparation failed')
        if fault in ('outage','busy','late'):time.sleep(1.2)
        if fault in ('cancel','denied','reclaimed','stop'):
            for thread in threading.enumerate():
                if thread.name=='startup-lease-'+task['execution_id']:
                    thread.join(timeout=3)
        state['created']=True
    monkeypatch.setattr(worker,'create',create)
    if fault=='denied':
        with pytest.raises(client.APIError,match='denied'):worker.execute(task)
    else:worker.execute(task)
    assert not state['started']
    assert not any(t.name=='startup-lease-'+task['execution_id'] for t in threading.enumerate())
    if fault=='cancel':
        assert next(b for p,b in events if p.endswith('/complete'))['status']=='CANCELLED'
    if fault=='reclaimed':assert any(p.endswith('/cleanup') for p,b in events)
    if fault=='create-error':
        assert next(b for p,b in events if p.endswith('/complete'))['error']=='preparation failed'


@pytest.mark.parametrize('error',[TimeoutError('temporary'),urllib.error.URLError('temporary'),client.APIError(503,'busy')])
def test_transient_startup_renewal_recovers_within_lease(execution,monkeypatch,error):
    worker,task,events,state=setup(execution,monkeypatch,lease=4)
    recovered=threading.Event();original=worker.api.post;beats=0
    def post(path,body=None):
        nonlocal beats
        if path=='worker/heartbeat' and body.get('started') is False:
            beats+=1
            if beats==2:raise error
            if beats==3:recovered.set()
        return original(path,body)
    worker.api.post=post
    def create(task):
        assert recovered.wait(4)
        state['created']=True
    monkeypatch.setattr(worker,'create',create)
    worker.execute(task)
    assert next(b for p,b in events if p.endswith('/complete'))['status']=='SUCCEEDED'


def test_three_sequential_executions_have_independent_startup_renewal(execution,monkeypatch):
    import uuid
    worker,task,events,state=setup(execution,monkeypatch)
    renewed=threading.Event();original=worker.api.post;counts={}
    def post(path,body=None):
        if path=='worker/heartbeat' and body.get('started') is False:
            identity=body['execution_id']
            counts[identity]=counts.get(identity,0)+1
            if counts[identity]>=2:renewed.set()
        return original(path,body)
    worker.api.post=post
    def create(task):
        assert renewed.wait(3)
        state['created']=True
    monkeypatch.setattr(worker,'create',create)
    for _ in range(3):
        task={**task,'execution_id':str(uuid.uuid4())}
        state.update(created=False,started=False);renewed.clear()
        worker.execute(task)
        assert worker.state is None
        assert not any(t.name=='startup-lease-'+task['execution_id'] for t in threading.enumerate())
    assert len(counts)==3 and all(count>=2 for count in counts.values())
    assert [b['status'] for p,b in events if p.endswith('/complete')]==['SUCCEEDED']*3


def test_late_startup_reply_does_not_revive_already_started_process(execution,monkeypatch):
    worker,task,events,state=setup(execution,monkeypatch)
    request_pending=threading.Event();original=worker.api.post;beats=0
    def post(path,body=None):
        nonlocal beats
        if path=='worker/heartbeat' and body.get('started') is False:
            beats+=1
            if beats==2:
                request_pending.set()
                time.sleep(1.2)
        return original(path,body)
    worker.api.post=post
    def create(task):
        assert request_pending.wait(3)
        state['created']=True
    monkeypatch.setattr(worker,'create',create)
    worker.execute(task)
    assert state['started']  # Lease was still valid when start was issued.
    completion=next(b for p,b in events if p.endswith('/complete'))
    assert completion['status']=='FAILED' and 'lease expired' in completion['error']
    assert any(p=='stop' for p,b in events)
    assert not any(p=='worker/heartbeat' and b.get('started') is not False for p,b in events)
