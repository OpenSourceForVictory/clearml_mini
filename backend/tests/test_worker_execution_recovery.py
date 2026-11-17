"""Exercise Worker execution/cleanup with Docker and API as fault-injection boundaries."""
import json
import uuid
import urllib.error
from types import SimpleNamespace

import pytest
from worker import client


@pytest.fixture
def execution(tmp_path,monkeypatch):
    snapshot={'revision':0,'config':{'workspace_root':str(tmp_path)}}
    worker=client.Worker({'runner_image':'mock:1'},tmp_path/'state.json',snapshot=snapshot)
    worker.slot_id='slot'
    task={'execution_id':str(uuid.uuid4()),'lease_seconds':60,'status':'CLAIMED','benchmark':'mock'}
    events=[]
    def post(path,body=None):
        events.append((path,body));return {'lease_seconds':60,'cancel':False}
    worker.api.post=post
    monkeypatch.setattr(worker,'create',lambda task:events.append(('create',task)))
    monkeypatch.setattr(worker,'stop_container',lambda e:events.append(('stop',e)))
    monkeypatch.setattr(worker,'remove',lambda e:events.append(('remove',e)))
    monkeypatch.setattr(worker,'inspect',lambda e:{'State':{'Status':'exited','Running':False,'ExitCode':0}})
    monkeypatch.setattr(client,'docker',lambda *a,**kw:SimpleNamespace(stdout='',stderr=''))
    return worker,task,events


@pytest.mark.parametrize('mode,code,output,expected',[
    ('evaluation',0,'{"final":{"success_rate":1,"episode_count":2,"runtime":0.1}}\n','SUCCEEDED'),
    ('evaluation',0,'plain successful exit\n','FAILED'),
    ('evaluation',1,'{"final":{"success_rate":1,"episode_count":2,"runtime":0.1}}\n','FAILED'),
    ('evaluation',0,'{"final":null}\n[]\ninvalid\n','FAILED'),
    ('command',0,'ordinary command\n','SUCCEEDED'),('command',2,'ordinary command\n','FAILED')])
def test_completion_semantics(execution,monkeypatch,mode,code,output,expected):
    worker,task,events=execution
    if mode=='command':task['client_spec']={'template':{'task_type':'command'}}
    monkeypatch.setattr(worker,'inspect',lambda e:{'State':{'Status':'exited','Running':False,'ExitCode':code}})
    monkeypatch.setattr(client,'docker',lambda *a,**kw:SimpleNamespace(stdout=output,stderr=''))
    worker.execute(task)
    completion=next(body for path,body in events if path.endswith('/complete'))
    assert completion['status']==expected
    assert (completion['metrics'] is not None)==(mode=='evaluation' and expected=='SUCCEEDED')
    assert worker.state is None and json.loads(worker.path.read_text()) is None
    assert events[-1][0]=='remove'


def test_initial_cancellation_never_creates_or_inspects(execution,monkeypatch):
    worker,task,events=execution
    original=worker.api.post
    def post(path,body=None):return {**original(path,body),'cancel':True}
    worker.api.post=post
    monkeypatch.setattr(worker,'inspect',lambda *a:pytest.fail('cancelled work inspected'))
    worker.execute(task)
    assert [path for path,body in events]==['worker/heartbeat','stop',f"worker/executions/{task['execution_id']}/complete",'remove']
    assert events[2][1]['status']=='CANCELLED'


@pytest.mark.parametrize('status',[400,404,409,413,422,500])
def test_api_rejection_cleanup_and_retry_journal(execution,status):
    worker,task,events=execution
    original=worker.api.post
    def post(path,body=None):
        if path=='worker/heartbeat':raise client.APIError(status,'rejected')
        return original(path,body)
    worker.api.post=post
    if status==500:
        with pytest.raises(client.APIError):worker.execute(task)
        assert worker.state['task']==task and not any(p=='remove' for p,b in events)
    else:
        worker.execute(task)
        assert worker.state is None
        assert any(p.endswith('/cleanup' if status in (404,409) else '/complete') for p,b in events)
    assert events[0][0]=='stop'


@pytest.mark.parametrize('failure',[TimeoutError('offline'),urllib.error.URLError('offline'),OSError('disk')])
def test_transport_failure_stops_execution_but_retains_recovery_journal(execution,failure):
    worker,task,events=execution
    def post(*a,**kw):raise failure
    worker.api.post=post
    with pytest.raises(type(failure)):worker.execute(task)
    assert worker.state['task']==task and json.loads(worker.path.read_text())['task']==task
    assert events==[('stop',task['execution_id'])]


def test_transient_control_busy_heartbeat_keeps_existing_execution(execution,monkeypatch):
    worker,task,events=execution
    heartbeats=0
    original=worker.api.post
    def post(path,body=None):
        nonlocal heartbeats
        if path=='worker/heartbeat':
            heartbeats+=1
            if heartbeats==2:raise client.APIError(503,'control plane busy; retry shortly')
        return original(path,body)
    worker.api.post=post
    inspections=0
    def inspect(_):
        nonlocal inspections
        inspections+=1
        if inspections==1:return {'State':{'Status':'created','Running':False,'ExitCode':0}}
        return {'State':{'Status':'exited','Running':False,'ExitCode':0}}
    monkeypatch.setattr(worker,'inspect',inspect)
    monkeypatch.setattr(client.time,'sleep',lambda _:None)
    worker.execute(task)
    assert heartbeats>=3
    assert len([p for p,_ in events if p=='create'])==0
    assert any(p.endswith('/complete') for p,_ in events)
    assert worker.state is None


@pytest.mark.parametrize('mode,expected',[('docker','Docker container'),('host','host unit')])
def test_missing_running_container_is_never_recreated(execution,monkeypatch,mode,expected):
    worker,task,events=execution;task['status']='RUNNING'
    if mode=='host':task['client_spec']={'template':{'execution_mode':'host'}}
    monkeypatch.setattr(worker,'inspect',lambda e:None)
    worker.execute(task)
    assert not any(p=='create' for p,b in events)
    completion=next(b for p,b in events if p.endswith('/complete'))
    assert completion['status']=='FAILED' and expected in completion['error'] and 'refusing duplicate' in completion['error']


@pytest.mark.parametrize('exit_record,expected_status,expected_error',[
    ('{"code":0}', 'SUCCEEDED', None),
    ('{"code":7}', 'FAILED', 'runner exited with code 7'),
    (None, 'FAILED', 'previously created host unit is missing'),
    ('{"code":"invalid"}', 'FAILED', 'invalid host execution exit record'),
])
def test_host_recovery_uses_durable_exit_record_without_relaunch(
    execution,monkeypatch,exit_record,expected_status,expected_error
):
    from worker.host_backend import HostBackend
    worker,task,events=execution
    task.update(status='RUNNING',client_spec={'template':{'execution_mode':'host','task_type':'command'}})
    worker.state={'task':task,'created':True,'offset':0,'sequence':0,'pending':None}
    worker.save()
    host=HostBackend(worker)
    host.root.mkdir(parents=True,exist_ok=True)
    (host.root/'launch.json').write_text(json.dumps({'execution_id':task['execution_id'],'started':True}))
    if exit_record is not None:(host.root/'exit.json').write_text(exit_record)
    monkeypatch.setattr(HostBackend,'control',lambda *a,**kw:SimpleNamespace(stdout='LoadState=not-found\n',returncode=0))
    monkeypatch.setattr(worker,'inspect',lambda execution_id:HostBackend(worker).inspect(execution_id))
    monkeypatch.setattr(HostBackend,'start',lambda *a:pytest.fail('recovery must not relaunch the business program'))
    worker.execute(task)
    completion=next(body for path,body in events if path.endswith('/complete'))
    assert completion['status']==expected_status
    assert (expected_error in completion['error']) if expected_error else completion['error'] is None
    assert not any(path=='create' for path,_ in events)
    assert worker.state is None and json.loads(worker.path.read_text()) is None


def test_rejected_failure_completion_uses_cleanup(execution,monkeypatch):
    worker,task,events=execution
    original=worker.api.post
    monkeypatch.setattr(worker,'inspect',lambda e:None);task['status']='RUNNING'
    def post(path,body=None):
        if path.endswith('/complete'):raise client.APIError(409,'lease expired')
        return original(path,body)
    worker.api.post=post;worker.execute(task)
    assert any(p.endswith('/cleanup') for p,b in events) and worker.state is None


def test_shutdown_isolates_failed_stop_and_stops_all_children(execution):
    worker,task,events=execution
    closed=[]
    worker.control=SimpleNamespace(stopping=False)
    worker.retention=SimpleNamespace(close=lambda:closed.append('retention'))
    import threading
    worker.runtime=SimpleNamespace(stopping=threading.Event(),close=lambda:closed.append('runtime'))
    def fail(e):raise RuntimeError('unit unavailable')
    first=SimpleNamespace(stop_event=threading.Event(),stop_container=fail)
    second=SimpleNamespace(stop_event=threading.Event(),stop_container=lambda e:closed.append(e))
    worker.children={'first':first,'second':second}
    worker.threads={'first':SimpleNamespace(join=lambda timeout:closed.append(timeout))}
    worker.shutdown()
    assert worker.control.stopping and worker.runtime.stopping.is_set()
    assert first.stop_event.is_set() and second.stop_event.is_set()
    assert closed==['retention','second',20,'runtime']


def test_launch_single_owner_and_journal_retained_on_failure(execution):
    import threading
    worker,task,events=execution;entered=threading.Event();release=threading.Event();calls=[]
    def execute(task):
        calls.append(task);entered.set();assert release.wait(2);raise TimeoutError('temporary outage')
    child=SimpleNamespace(execute=execute,state={'task':task})
    worker.launch(child,task);assert entered.wait(1)
    thread=worker.threads[task['execution_id']]
    worker.launch(child,task)
    assert worker.threads[task['execution_id']] is thread
    release.set();thread.join(2)
    assert calls==[task] and child.state=={'task':task}


def test_once_loop_registers_claims_and_releases_completed_child(execution,monkeypatch):
    from worker import accelerators,control,probe_credentials,version
    worker,task,events=execution;worker.config['mock']=True;worker.config['node_name']='virtual-test'
    control_events=[];retention_events=[]
    controller=SimpleNamespace(source=None,refresh=lambda *a:control_events.append('refresh'),
        adopt=lambda:control_events.append('adopt'),report=lambda:{},ready=lambda:True)
    monkeypatch.setattr(control,'Control',lambda *a:controller)
    monkeypatch.setattr(accelerators,'capabilities',lambda cfg:[])
    monkeypatch.setattr(probe_credentials,'available',lambda cfg:[])
    monkeypatch.setattr(client,'Retention',lambda *a:SimpleNamespace(start=lambda:None,
        remember=lambda root:retention_events.append(root),mark_ready=lambda e:retention_events.append(e)))
    monkeypatch.setattr(client.time,'sleep',lambda seconds:None)
    calls=[]
    def post(path,body=None):
        calls.append((path,body))
        if path=='worker/register':return {'slot_id':str(uuid.uuid4()),'resource_source':'agent'}
        if path.endswith('/claim'):return {'cleanup':[],'task':task}
        return {'resource_source':'agent'}
    worker.api.post=post
    def launch(child,task):
        child.state=None;worker.children[task['execution_id']]=child
    monkeypatch.setattr(worker,'launch',launch)
    try:worker.run(once=True)
    finally:
        if hasattr(worker,'slot_lock'):worker.slot_lock.close()
    registration=calls[0][1]
    assert registration['protocol_version']==version.PROTOCOL_VERSION and registration['gpu_model']=='virtual'
    assert registration['software']['version']==version.SOFTWARE_VERSION
    assert any(p.endswith('/claim') for p,b in calls)
    assert retention_events==[str(worker.workspace),task['execution_id']]
    assert worker.children=={} and controller.source=='agent'

@pytest.mark.parametrize('outcome',['success','cancel','expired','stop','denied'])
def test_initial_busy_handshake_is_bounded_and_never_launches_early(execution,monkeypatch,outcome):
    worker,task,events=execution
    original=worker.api.post
    attempts=0
    clock=[10.]
    monkeypatch.setattr(client.time,'monotonic',lambda:clock[0])
    def wait(seconds):
        assert not any(p in ('create','inspect') for p,_ in events)
        if outcome=='expired':clock[0]+=60
        elif outcome=='stop':worker.stop_event.set()
        return worker.stop_event.is_set()
    monkeypatch.setattr(worker.stop_event,'wait',wait)
    def post(path,body=None):
        nonlocal attempts
        if path=='worker/heartbeat' and body.get('started') is False:
            attempts+=1
            if attempts==1:raise client.APIError(503,'busy')
            if outcome=='denied':raise client.APIError(401,'denied')
            return {**original(path,body),'cancel':outcome=='cancel'}
        return original(path,body)
    worker.api.post=post
    if outcome=='denied':
        with pytest.raises(client.APIError):worker.execute(task)
    else:worker.execute(task)
    assert attempts==(1 if outcome in ('expired','stop') else 2)
    assert not any(p=='create' for p,_ in events)
    if outcome in ('cancel','expired','stop'):
        completion=next(b for p,b in events if p.endswith('/complete'))
        assert completion['status']==('CANCELLED' if outcome=='cancel' else 'FAILED')
    if outcome=='success':assert worker.state is None

def test_initial_busy_then_launches_new_execution_once(execution,monkeypatch):
    worker,task,events=execution
    task['client_spec']={'template':{'task_type':'command'}}
    original=worker.api.post;attempts=0
    def post(path,body=None):
        nonlocal attempts
        if path=='worker/heartbeat' and body.get('started') is False:
            attempts+=1
            if attempts<3:raise client.APIError(503,'busy')
        return original(path,body)
    worker.api.post=post
    monkeypatch.setattr(worker.stop_event,'wait',lambda _:False)
    states=iter([None,{'State':{'Status':'created','Running':False}},
                 {'State':{'Status':'exited','Running':False,'ExitCode':0}}])
    monkeypatch.setattr(worker,'inspect',lambda _:next(states))
    worker.execute(task)
    assert attempts==3
    assert len([p for p,_ in events if p=='create'])==1
    assert next(b for p,b in events if p.endswith('/complete'))['status']=='SUCCEEDED'
