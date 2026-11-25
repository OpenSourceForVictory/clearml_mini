import copy
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

from worker.client import Worker
from worker.recovery_monitor import RecoveryMonitor
from worker.recovery_runtime import RecoveryRuntime
from worker.storage import atomic_json


@pytest.fixture
def case(tmp_path, monkeypatch):
    w = object.__new__(Worker)
    w.path = tmp_path/'worker.json'
    w.workspace = tmp_path/'workspace'
    (w.workspace/'runs').mkdir(parents=True)
    w.config = {}
    w.slot_id = 's'
    w.start_lock = threading.Lock()
    w.stop_event = threading.Event()
    w.host_mode = lambda:False
    execution = str(uuid.uuid4())
    boot = Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    task = {'execution_id':execution, 'benchmark':'cs-client', 'timeout_seconds':0,
            'client_spec':{'template':{'task_type':'command','integration_mode':'sdk-command'}}}
    receipt = {'execution_id':execution, 'generation':'g', 'request_id':str(uuid.uuid4()),
               'disposition':'reconcile', 'task':copy.deepcopy(task),
               'policy':{'disconnect_tolerance_seconds':None}}
    w.state = {'task':task, 'created':False, 'offset':0, 'sequence':0, 'pending':None,
               'recovery_assignment':{'reconciliation_required':True, 'claim':{
                   'slot_id':'s', 'boot_id':boot, 'requested_at':time.monotonic(), 'receipt':receipt}}}
    atomic_json(w.path, w.state)
    raw = [None]
    calls = []
    output = ['hello\n', '']
    fault = {'outage':False, 'cancel':False, 'results':False, 'logs':False, 'lose_completion':False}
    completed = threading.Event()
    def create(task):
        calls.append('create')
        raw[0] = {'Id':'a'*64, 'Config':{'Labels':{'mini-eval.launch':task['recovery_launch_id']}},
                  'State':{'Status':'created', 'Running':False}}
    w.create = create
    w.inspect = lambda e:copy.deepcopy(raw[0])
    def start(runtime, marker):
        calls.append('start')
        raw[0]['State'].update(Status='running', Running=True)
        return runtime.inspect_launch(marker)
    monkeypatch.setattr(RecoveryRuntime, 'start', start)
    def stop(runtime, identity):
        assert runtime.inspect()['identity'] == identity
        calls.append('stop')
        raw[0]['State'].update(Status='exited', Running=False, ExitCode=0)
        runtime.inspect()
    monkeypatch.setattr(RecoveryRuntime, 'stop', stop)
    def remove(runtime, identity):
        assert runtime.inspect()['state'] == 'exited'
        calls.append('remove')
        raw[0] = None
    monkeypatch.setattr(RecoveryRuntime, 'remove', remove)
    monkeypatch.setattr('worker.recovery_execution.output', lambda worker:tuple(output))
    def monitor(launch, control=False):
        m = RecoveryMonitor(w, launch, interval=.01)
        if control:m.start_control(w.api, interval=.02)
        return m
    w.monitor_recovery = monitor
    def post(path, body=None):
        calls.append((path, copy.deepcopy(body)))
        if path.endswith('/recovery/prepare'):
            return {'execution_id':execution, **body, 'action':'launch', 'reason':None,
                    'duplicate':False, 'valid_for_seconds':30}
        if path.endswith('/recovery/reconcile'):
            if fault['outage']:raise TimeoutError('offline')
            return {'execution_id':execution,'generation':'g','sequence':body['sequence'],
                    'duplicate':False,'action':'stop' if fault['cancel'] else 'track',
                    'reason':'cancelled' if fault['cancel'] else None}
        if path.endswith('/recovery/results'):
            if fault['results']:raise TimeoutError('result path offline')
            from worker.recovery_results import execution_outbox
            record = next(r for r in execution_outbox(w).pending() if r['sequence']==body['sequence'])
            return {'digest':record['digest'],'reconciled':True}
        if path.endswith('/complete'):
            calls.append('complete')
            assert any(isinstance(c,tuple) and c[0].endswith('/recovery/reconcile') and c[1]['state']=='exited' for c in calls)
            if fault['lose_completion']:
                fault['lose_completion']=False
                raise TimeoutError('lost completion response')
            return {'status':'CANCELLED' if fault['cancel'] else body['status'], 'cleanup_confirmed':False,'duplicate':False}
        if path.endswith('/cleanup'):
            assert raw[0] is None
            calls.append('cleanup');completed.set()
            return {'status':'SUCCEEDED','cleanup_confirmed':True,'duplicate':False}
        if path.endswith('/recovery/results-finished'):
            return {'status':'SUCCEEDED','cleanup_confirmed':True,'duplicate':False,'result_sync':'complete'}
        if path.endswith('/logs'):
            if fault['logs']:raise TimeoutError('logs offline')
            return {}
        raise AssertionError(path)
    w.api = SimpleNamespace(post=post)
    def exit_program(code=0):
        raw[0]['State'].update(Status='exited',Running=False,ExitCode=code)
    return SimpleNamespace(worker=w, raw=raw, calls=calls, output=output, fault=fault,
                           completed=completed, exit=exit_program)


def launch(case):
    errors = []
    def run():
        try:case.worker.execute(case.worker.state['task'])
        except BaseException as e:errors.append(e)
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    wait_for(lambda:'start' in case.calls)
    return thread, errors


def wait_for(predicate, timeout=5):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.01)
    raise AssertionError('condition not reached')


def finish(case, thread, errors):
    thread.join(6)
    if thread.is_alive():
        case.worker.stop_event.set();thread.join(6)
        pytest.fail('main execution loop did not finish')
    assert not errors, errors


@pytest.mark.parametrize('code',[0,1])
def test_execute_full_main_loop_completes_and_removes_once(case, code):
    thread, errors = launch(case)
    case.exit(code)
    finish(case,thread,errors)
    assert case.worker.state is None and case.calls.count('start')==1
    assert case.calls.index('complete') < case.calls.index('remove') < case.calls.index('cleanup')
    body=next(c[1] for c in case.calls if isinstance(c,tuple) and c[0].endswith('/complete'))
    assert body['exit_code']==code and body['status']==('SUCCEEDED' if code==0 else 'FAILED')


def test_offline_running_then_reconnect_cancel(case):
    case.fault['outage']=True
    thread,errors=launch(case)
    time.sleep(.7)
    assert case.raw[0]['State']['Running'] and 'stop' not in case.calls
    case.fault.update(outage=False,cancel=True)
    finish(case,thread,errors)
    assert case.calls.count('stop')==1 and case.calls.count('start')==1


def test_exit_offline_preserves_source_then_reconnects(case):
    case.fault['outage']=True
    thread,errors=launch(case);case.exit()
    wait_for(lambda:Path(str(case.worker.path)+'.recovery-output').exists())
    assert not case.completed.is_set() and case.raw[0] is not None
    case.fault['outage']=False
    finish(case,thread,errors)
    assert case.worker.state is None


def test_result_and_log_outage_cannot_prevent_cleanup(case):
    case.output[0]='MINI_EVAL_EVENT '+json.dumps({'episode_id':1,'seed':1,'status':'STARTED'})+'\n'
    case.fault.update(results=True,logs=True)
    thread,errors=launch(case);case.exit()
    thread.join(6)
    assert not thread.is_alive() and errors and isinstance(errors[0],TimeoutError)
    assert case.completed.is_set() and case.worker.state['recovery_finalization']['phase']=='done'
    case.fault['results']=False
    case.worker.execute(case.worker.state['task'])
    assert case.worker.state is None and case.calls.count('start')==1


def test_completion_ack_lost_reentry_does_not_launch(case):
    case.fault['lose_completion']=True
    thread,errors=launch(case);case.exit();thread.join(6)
    assert errors and isinstance(errors[0],TimeoutError)
    assert case.worker.state['recovery_finalization']['phase']=='completion'
    case.worker.execute(case.worker.state['task'])
    assert case.worker.state is None and case.calls.count('start')==1 and case.calls.count('remove')==1


def test_worker_exit_retains_program_and_restart_binds_original(case):
    thread,errors=launch(case)
    case.worker.stop_event.set();finish(case,thread,errors)
    assert case.raw[0]['State']['Running'] and 'stop' not in case.calls
    case.worker.state=json.loads(case.worker.path.read_text())
    case.worker.stop_event.clear()
    thread,errors=launch(case);case.exit();finish(case,thread,errors)
    assert case.calls.count('start')==1


def test_old_running_ack_cannot_authorize_completion(case):
    original=case.worker.api.post;entered=threading.Event();release=threading.Event()
    def post(path,body=None):
        if path.endswith('/recovery/reconcile') and body['state']=='running':
            entered.set();assert release.wait(5)
        return original(path,body)
    case.worker.api.post=post
    thread,errors=launch(case)
    try:
        assert entered.wait(3);case.exit();time.sleep(.6)
        assert not case.completed.is_set()
    finally:release.set()
    finish(case,thread,errors)


def test_final_collection_outbox_full_preserves_raw_source_after_cleanup(case,monkeypatch):
    from worker.reliable_outbox import OutboxFull
    original=case.worker.collect_recovery_results
    full=[True]
    def collect(*args,**kwargs):
        if full[0]:raise OutboxFull('full')
        return original(*args,**kwargs)
    case.worker.collect_recovery_results=collect
    thread,errors=launch(case);case.exit();thread.join(6)
    assert errors and isinstance(errors[0],OutboxFull)
    assert case.completed.is_set() and case.raw[0] is None
    assert Path(str(case.worker.path)+'.recovery-output').exists()
    full[0]=False
    case.worker.execute(case.worker.state['task'])
    assert case.worker.state is None and case.calls.count('start')==1


def test_saved_output_identity_is_checked(case):
    from worker.recovery_execution import resume_collection
    case.worker.state['recovery']={'generation':'g','identity':{}}
    atomic_json(Path(str(case.worker.path)+'.recovery-output'),
                {'execution_id':'wrong','proof':{},'stdout':''})
    with pytest.raises(ValueError,match='original execution'):resume_collection(case.worker,None)


def test_data_request_blocked_control_still_receives_cancel(case):
    post=case.worker.api.post;entered=threading.Event();release=threading.Event()
    def slow(path,body=None):
        if path.endswith('/logs'):
            entered.set();assert release.wait(5)
        return post(path,body)
    case.worker.api.post=slow
    thread,errors=launch(case)
    try:
        assert entered.wait(3)
        case.fault['cancel']=True
        wait_for(lambda:'stop' in case.calls)
        assert not case.raw[0]['State']['Running']
    finally:release.set()
    finish(case,thread,errors)


def test_shutdown_does_not_stop_negotiated_but_stops_legacy(case):
    parent=object.__new__(Worker)
    parent.process_logs=parent.control=parent.retention=parent.runtime=None
    stops=[]
    legacy=SimpleNamespace(state={'task':{}},stop_event=threading.Event(),stop_container=lambda e:stops.append(e))
    case.worker.stop_container=lambda e:pytest.fail('must not stop negotiated execution on Worker restart')
    parent.children={'new':case.worker,'legacy':legacy};parent.threads={}
    parent.slot_lock=None
    parent.shutdown()
    assert stops==['legacy'] and case.worker.stop_event.is_set()


def test_service_probe_rebuilds_epoch_without_stopping_business(case,monkeypatch):
    from worker.recovery_execution import ServiceProbe
    w=case.worker;task=w.state['task'];task['benchmark']='inference-service'
    task['client_spec']['service']={'readiness':{'startup_timeout':1}}
    runners=[];sessions=[]
    def runner(*args):
        r=SimpleNamespace(last={'ready':True},error=None,close=lambda:sessions.append('closed'))
        runners.append(r);return r
    monkeypatch.setattr('worker.readiness.ProbeRunner',runner)
    def post(path,*args):
        sessions.append(path);return {'epoch':str(len(sessions)),'ever_ready':False}
    w.api.post=post
    probe=ServiceProbe(w,time.monotonic()-10)
    probe.step()
    assert w.state['service_was_ready'] and 'recovery_local_failure' not in w.state
    runners[0].error='service probe report rejected'
    probe.step()
    assert len(runners)==2 and 'closed' in sessions
    probe.close()


def test_service_actual_probe_failure_uses_original_start_deadline(case,monkeypatch):
    from worker.recovery_execution import ServiceProbe
    w=case.worker;w.state['task'].update(benchmark='inference-service')
    w.state['task']['client_spec']['service']={'startup_timeout':1}
    monkeypatch.setattr('worker.recovery_execution.health',lambda s:{'ready':False,'error':'unready'})
    w.api.post=lambda *args:(_ for _ in ()).throw(TimeoutError('report offline'))
    with pytest.raises(TimeoutError):ServiceProbe(w,time.monotonic()-10).step()
    assert w.state['recovery_local_failure']=='service startup health deadline exceeded'


@pytest.mark.parametrize('sender',[False,True])
def test_protocol_output_paths(case,monkeypatch,sender):
    monkeypatch.undo() # Exercise real filesystem readers; no execution is launched.
    from worker.recovery_execution import output,protocol_root
    w=case.worker
    if sender:
        w.state['task']['log_protocol']=2
        root=protocol_root(w);root.mkdir(parents=True)
        assert output(w)==('','')
        (root/'final.json').write_text('{"final":{}}')
        assert output(w)==('{"final":{}}','')
    else:
        monkeypatch.setattr('worker.client.docker',lambda *args,**kwargs:SimpleNamespace(stdout='out',stderr='err'))
        assert output(w)==('out','err')


@pytest.mark.parametrize('kind',['missing','identity'])
def test_unknown_runtime_retains_journal_no_cleanup_or_restart(case,kind):
    thread,errors=launch(case)
    if kind=='missing':case.raw[0]=None
    else:case.raw[0]['Id']='b'*64
    time.sleep(.7)
    case.worker.stop_event.set();finish(case,thread,errors)
    assert case.worker.state and not case.completed.is_set()
    assert case.calls.count('start')==1 and 'remove' not in case.calls


def test_service_startup_failure_stops_original_then_finalizes(case,monkeypatch):
    task=case.worker.state['task'];task['benchmark']='inference-service'
    task['client_spec']['service']={'startup_timeout':0}
    case.worker.state['recovery_assignment']['claim']['receipt']['task']=copy.deepcopy(task)
    monkeypatch.setattr('worker.recovery_execution.health',lambda s:{'ready':False,'error':'not listening'})
    original=case.worker.api.post
    case.worker.api.post=lambda p,b=None: {} if p.endswith('/service-health') else original(p,b)
    thread,errors=launch(case);finish(case,thread,errors)
    assert case.calls.count('stop')==1
    body=next(c[1] for c in case.calls if isinstance(c,tuple) and c[0].endswith('/complete'))
    assert body['status']=='FAILED' and body['error']=='service startup health deadline exceeded'


def test_service_report_rejection_does_not_kill_healthy_program(case,monkeypatch):
    task=case.worker.state['task'];task['benchmark']='inference-service'
    task['client_spec']['service']={'startup_timeout':0}
    case.worker.state['recovery_assignment']['claim']['receipt']['task']=copy.deepcopy(task)
    monkeypatch.setattr('worker.recovery_execution.health',lambda s:{'ready':True,'error':None})
    thread,errors=launch(case);time.sleep(.6)
    assert case.raw[0]['State']['Running'] and 'stop' not in case.calls
    case.exit();finish(case,thread,errors)


@pytest.mark.parametrize('host',[False,True])
def test_v2_log_lifecycle_does_not_wait_for_sender(case,monkeypatch,host):
    task=case.worker.state['task'];task['log_protocol']=2
    case.worker.state['recovery_assignment']['claim']['receipt']['task']=copy.deepcopy(task)
    if host:
        case.worker.host_mode=lambda:True
        raw_method=RecoveryRuntime._raw
        monkeypatch.setattr(RecoveryRuntime,'_raw',lambda self:case.worker.inspect(task['execution_id']))
        original=case.worker.create
        def create(task):
            original(task)
            case.raw[0].update(InvocationID='b'*32,LaunchID=task['recovery_launch_id'])
        case.worker.create=create
    events=[]
    class Collector:
        def __init__(self,*a,**kw):events.append('collector')
        def close(self):events.append('collector-close')
    class Sender:
        def __init__(self,*a,**kw):events.append('sender')
        def finish(self,timeout):assert timeout==0;return False
        def close(self):events.append('sender-close')
    monkeypatch.setattr('worker.log_stream.Sender',Sender)
    monkeypatch.setattr('worker.log_stream.DockerCollector',Collector)
    thread,errors=launch(case);case.exit();finish(case,thread,errors)
    assert ('collector' in events)==(not host)
    assert 'sender-close' in events
    body=next(c[1] for c in case.calls if isinstance(c,tuple) and c[0].endswith('/complete'))
    assert body['log_gap'] is True


def test_completion_never_turns_protocol_absence_into_evaluation_success():
    from worker.recovery_execution import completion
    task={'benchmark':'cs-client','client_spec':{'template':{}}}
    snapshot={'exit_code':0,'control':{'directive':'retain'},'stop_reason':None}
    body=completion(task,snapshot,'no result\n','')
    assert body['status']=='FAILED' and body['exit_code']==0 and 'missing protocol final' in body['error']
    body=completion(task,snapshot,'null\n'+json.dumps({'final':{'success_rate':1,'episode_count':1,'runtime':2}}),'')
    assert body['status']=='SUCCEEDED'


def test_probe_initial_session_remembers_platform_ever_ready(case,monkeypatch):
    from worker.recovery_execution import ServiceProbe
    w=case.worker;w.state['task']['benchmark']='inference-service'
    w.state['task']['client_spec']['service']={'readiness':{'startup_timeout':1}}
    w.api.post=lambda *a:{'epoch':'new','ever_ready':True}
    monkeypatch.setattr('worker.readiness.ProbeRunner',lambda *a:SimpleNamespace(last={'ready':False},error=None,close=lambda:None))
    probe=ServiceProbe(w,time.monotonic()-20);probe.step();probe.close()
    assert w.state['service_was_ready'] and not w.state.get('recovery_local_failure')


@pytest.mark.parametrize('changes',[
    {'episode_count':0},{'episode_count':2},{'episode_count':True},{'runtime':-1},
    {'runtime':float('nan')},{'runtime':10**400},{'runtime':True},{'success_rate':2},{'success_rate':'1'}])
def test_invalid_final_metrics_cannot_hold_control_completion_forever(changes):
    from worker.recovery_execution import completion
    task={'benchmark':'cs-client','episodes':1,'client_spec':{'template':{}}}
    snapshot={'exit_code':0,'control':{'directive':'retain'},'stop_reason':None}
    record={'final':{'episode_count':1,'runtime':1,'success_rate':1,**changes}}
    body=completion(task,snapshot,json.dumps(record),'')
    assert body['status']=='FAILED' and body['metrics'] is None and body['exit_code']==0


def test_already_collected_source_does_not_need_raw_file(case):
    from worker.recovery_execution import resume_collection
    case.worker.state['recovery_results_collected']=True
    assert resume_collection(case.worker,None)


def test_main_loop_retires_journal_with_unterminated_diagnostic_output(case):
    case.output[0]='ordinary program output without newline'
    thread,errors=launch(case)
    case.exit()
    finish(case,thread,errors)
    assert case.worker.state is None
    assert case.calls.count('remove')==1
    assert any(isinstance(c,tuple) and c[0].endswith('/recovery/results-finished') for c in case.calls)
