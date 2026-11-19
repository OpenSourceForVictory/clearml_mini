"""Service/client orchestration faults at the Worker API and process boundaries."""
from types import SimpleNamespace

import pytest
from backend.tests.test_worker_execution_recovery import execution
from worker import client,readiness


@pytest.mark.parametrize('mode',['ready','previously-ready','deadline','probe-error','pending'])
def test_probe_session_recovery_and_shutdown(execution,monkeypatch,mode):
    worker,task,events=execution
    task.update(benchmark='inference-service',client_spec={'template':{},'service':{'readiness':{'startup_timeout':0 if mode=='deadline' else 60}}})
    probe=SimpleNamespace(error='probe crashed' if mode=='probe-error' else None,accepted_ready=mode=='ready',close=lambda:events.append(('probe-close',None)))
    monkeypatch.setattr(readiness,'ProbeRunner',lambda *a:probe)
    original=worker.api.post
    def post(path,body=None):
        if path.endswith('/service-probe-session'):
            worker.state['service_started_at']-=1
            return {'epoch':3,'ever_ready':mode=='previously-ready'}
        return original(path,body)
    worker.api.post=post
    worker.execute(task)
    completion=next(body for path,body in events if path.endswith('/complete'))
    assert completion['status']=='FAILED'  # A service exiting 0 is not an evaluation result.
    if mode=='deadline':assert 'startup health deadline' in completion['error']
    if mode=='probe-error':assert completion['error']=='probe crashed'
    assert worker.state is None
    if mode!='deadline':assert events[-1][0]=='probe-close'


@pytest.mark.parametrize('mode',['ready','health-outage','deadline','previously-ready'])
def test_legacy_health_and_transient_reporting(execution,monkeypatch,mode):
    worker,task,events=execution
    task.update(benchmark='inference-service',client_spec={'template':{},'service':{'startup_timeout':0 if mode=='deadline' else 60}})
    count=[0];original=worker.api.post
    def post(path,body=None):
        if path=='worker/heartbeat' and body.get('started') is not False:
            count[0]+=1
            worker.state['service_started_at']-=1
            if mode=='previously-ready':worker.state['service_was_ready']=True
        if path.endswith('/service-health') and mode=='health-outage':raise TimeoutError('response lost')
        return original(path,body)
    worker.api.post=post
    monkeypatch.setattr(client.time,'sleep',lambda delay:None)
    monkeypatch.setattr(worker,'inspect',lambda e:{'State':{'Status':'running','Running':count[0]<2,'ExitCode':0}})
    monkeypatch.setattr(client,'service_health',lambda service:{'ready':mode in ('ready','health-outage'),'error':None})
    worker.execute(task)
    completion=next(body for path,body in events if path.endswith('/complete'))
    assert completion['status']=='FAILED'
    if mode=='deadline':assert count[0]==1 and 'deadline' in completion['error']
    else:assert count[0]==2 and completion['error']=='runner exited with code 0'


@pytest.mark.parametrize('mode',['heartbeat-outage','log-outage','journal-backlog','stdout-backlog','log-backlog','cancel-unloaded-host','disappeared'])
def test_retry_delivery_and_cancelled_transient_units(execution,monkeypatch,mode):
    worker,task,events=execution
    task.update(benchmark='cs-client',client_spec={'service_mode':'none','template':{'episode_reporting':mode=='journal-backlog','task_type':'command'}})
    if mode=='cancel-unloaded-host':task['client_spec']['template']['execution_mode']='host'
    calls=[0];original=worker.api.post
    def post(path,body=None):
        if path=='worker/heartbeat' and body.get('started') is not False:
            calls[0]+=1
            if calls[0]==1 and mode=='heartbeat-outage':raise TimeoutError('temporary outage')
        result=original(path,body)
        if mode=='cancel-unloaded-host' and body and body.get('started') is not False:result['cancel']=True
        return result
    worker.api.post=post
    monkeypatch.setattr(client.time,'sleep',lambda delay:None)
    def inspect(e):
        if calls[0] and mode in ('disappeared','cancel-unloaded-host'):return None
        return {'State':{'Status':'exited','Running':False,'ExitCode':0}}
    monkeypatch.setattr(worker,'inspect',inspect)
    def logs(e,output):
        if calls[0]==1 and mode=='log-outage':raise TimeoutError('log response lost')
        return not (calls[0]==1 and mode=='log-backlog')
    monkeypatch.setattr(worker,'send_logs',logs)
    monkeypatch.setattr(worker,'send_episode_journal',lambda e:not(calls[0]==1 and mode=='journal-backlog'))
    monkeypatch.setattr(worker,'send_episodes',lambda e,out:not(calls[0]==1 and mode=='stdout-backlog'))
    monkeypatch.setattr(worker,'upload_artifacts',lambda *a:events.append(('artifacts',None)))
    monkeypatch.setattr(client,'HostBackend',lambda w:SimpleNamespace(logs=lambda:('','')))
    worker.execute(task)
    completion=next(body for path,body in events if path.endswith('/complete'))
    assert completion['status']==('CANCELLED' if mode=='cancel-unloaded-host' else 'FAILED' if mode=='disappeared' else 'SUCCEEDED')
    if mode in ('heartbeat-outage','log-outage','journal-backlog','stdout-backlog','log-backlog'):assert calls[0]==2
    assert worker.state is None


@pytest.mark.parametrize('status',[404,409,500])
def test_invalid_event_failure_acknowledgement_recovery(execution,status):
    worker,task,events=execution;original=worker.api.post
    def post(path,body=None):
        if path=='worker/heartbeat':raise client.APIError(422,'invalid event')
        if path.endswith('/complete'):raise client.APIError(status,'completion rejected')
        return original(path,body)
    worker.api.post=post
    if status==500:
        with pytest.raises(client.APIError):worker.execute(task)
        assert worker.state is not None
    else:
        worker.execute(task)
        assert any(path.endswith('/cleanup') for path,body in events) and worker.state is None


@pytest.mark.parametrize('stop_fails',[False,True])
def test_lease_watchdog_stops_process_while_api_call_is_blocked(execution,monkeypatch,stop_fails):
    import threading
    worker,task,events=execution;task['lease_seconds']=3
    stopped=threading.Event();stops=[];original=worker.api.post
    def stop(identity):
        stops.append(identity);stopped.set()
        if stop_fails and len(stops)==1:raise OSError('transient stop failure')
    monkeypatch.setattr(worker,'stop_container',stop)
    def post(path,body=None):
        if path=='worker/heartbeat':assert stopped.wait(3),'watchdog blocked behind API request'
        return original(path,body)
    worker.api.post=post
    worker.execute(task)
    assert stopped.is_set() and len(stops)>=2
    assert not any(path=='create' for path,body in events)
    result=next(body for path,body in events if path.endswith('/complete'))
    assert result['status']=='FAILED' and 'lease expired before start' in result['error']


@pytest.mark.parametrize('fault',['busy','timeout','network','rejected','deadline','stopped'])
@pytest.mark.parametrize('host',[False,True])
def test_probe_session_transient_failure_keeps_same_process(execution,monkeypatch,fault,host):
    import urllib.error
    worker,task,events=execution
    task.update(benchmark='inference-service',client_spec={'template':{'execution_mode':'host' if host else 'docker'},'service':{'readiness':{'startup_timeout':60}}})
    monkeypatch.setattr(worker,'host_mode',lambda:host)
    monkeypatch.setattr(readiness,'ProbeRunner',lambda *a:SimpleNamespace(error=None,accepted_ready=True,close=lambda:events.append(('probe-close',None))))
    original=worker.api.post
    calls=[]
    def post(path,body=None):
        if path.endswith('/service-probe-session'):
            calls.append(path)
            if len(calls)==1:
                if fault=='deadline':worker.state['service_started_at']-=61
                if fault=='stopped':worker.stop_event.set()
                if fault=='timeout':raise TimeoutError('temporary')
                if fault=='network':raise urllib.error.URLError('temporary')
                raise client.APIError(400 if fault=='rejected' else 503,'temporary')
            assert not any(p in ('stop','remove','create') for p,b in events)
            return {'epoch':'new','ever_ready':False}
        return original(path,body)
    worker.api.post=post
    worker.execute(task)
    completion=next(body for path,body in events if path.endswith('/complete'))
    assert not any(p=='create' for p,b in events)
    assert len(calls)==(2 if fault in ('busy','timeout','network') else 1)
    if fault=='deadline':assert 'startup health deadline' in completion['error']
    elif fault=='stopped':assert 'worker stopped' in completion['error']
    elif fault=='rejected':assert '400' in completion['error']
    else:assert completion['error']=='runner exited with code 0'
    assert worker.state is None


@pytest.mark.parametrize('outcome',['cancel','expired','recovered'])
def test_probe_session_wait_keeps_startup_lease_guard(execution,monkeypatch,outcome):
    import threading
    worker,task,events=execution
    task.update(benchmark='inference-service',client_spec={'template':{},'service':{'readiness':{'startup_timeout':60}}})
    renewed=threading.Event();calls=[];heartbeats=[]
    original=worker.api.post
    monkeypatch.setattr(readiness,'ProbeRunner',lambda *a:SimpleNamespace(error=None,accepted_ready=True,close=lambda:events.append(('probe-close',None))))
    def post(path,body=None):
        if path=='worker/heartbeat' and body.get('started') is False:
            heartbeats.append(body)
            reply=original(path,body)
            if len(heartbeats)>1:
                reply['cancel']=outcome=='cancel'
                if outcome=='expired':reply['lease_seconds']=1
                renewed.set()
            return reply
        if path.endswith('/service-probe-session'):
            calls.append(path)
            if len(calls)==1:
                assert renewed.wait(5), 'probe session blocked startup renewal'
                raise client.APIError(503,'control busy')
            assert not any(p in ('stop','create','remove') for p,b in events)
            return {'epoch':'recovered','ever_ready':False}
        return original(path,body)
    worker.api.post=post
    worker.execute(task)
    completion=next(body for path,body in events if path.endswith('/complete'))
    assert renewed.is_set() and not any(p=='create' for p,b in events)
    if outcome=='cancel':assert completion['status']=='CANCELLED'
    elif outcome=='expired':assert 'lease expired' in completion['error']
    else:assert len(calls)==2 and completion['error']=='runner exited with code 0'
    assert worker.state is None
