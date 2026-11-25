import copy
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import pytest

from worker.recovery_monitor import RecoveryMonitor
from worker.recovery_runtime import RecoveryRuntime


def wait(predicate):
    end=time.monotonic()+3
    while time.monotonic()<end:
        if predicate():return
        time.sleep(.005)
    assert predicate()


@pytest.fixture
def case(tmp_path,monkeypatch):
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    identity={'kind':'docker','boot_id':boot,'runtime_id':'a'*64}
    claim={'boot_id':boot,'slot_id':'slot','requested_at':0,
        'receipt':{'generation':'g','request_id':'r','policy':{'disconnect_tolerance_seconds':None}}}
    worker=SimpleNamespace(path=tmp_path/'worker',slot_id='slot',state={'task':{'execution_id':'e','timeout_seconds':0},
        'recovery_assignment':{'claim':claim},'recovery':{'generation':'g','identity':identity}})
    launch={'phase':'bound','identity':identity,'started_at':0,
        'assignment':{'execution_id':'e','generation':'g','request_id':'r'}}
    runtime={'state':'running','identity':copy.deepcopy(identity),'exit_code':None}
    stops=[];clock=[0]
    monkeypatch.setattr(RecoveryRuntime,'inspect',lambda self:copy.deepcopy(runtime))
    def stop(self,proof):
        assert proof==identity
        stops.append(proof);runtime.update(state='exited',exit_code=143)
    monkeypatch.setattr(RecoveryRuntime,'stop',stop)
    return worker,launch,runtime,stops,clock


def monitor(case):
    worker,launch,_,_,clock=case
    return RecoveryMonitor(worker,launch,interval=.005,clock=lambda:clock[0])


def reply(body,action='track'):
    return {'execution_id':'e','generation':'g','sequence':body['sequence'],'duplicate':False,'action':action,'reason':'cancelled' if action=='stop' else None}


@pytest.mark.parametrize('limit',['business','disconnect'])
def test_blocked_http_does_not_block_local_cutoff(case,limit):
    worker,_,runtime,stops,clock=case
    if limit=='business':worker.state['task']['timeout_seconds']=10
    else:worker.state['recovery_assignment']['claim']['receipt']['policy']['disconnect_tolerance_seconds']=10
    entered=threading.Event();release=threading.Event();errors=[]
    def post(path,body):
        entered.set();assert release.wait(3);return reply(body)
    m=monitor(case)
    def exchange():
        try:m.exchange(SimpleNamespace(post=post))
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=exchange)
    try:
        thread.start();assert entered.wait(2)
        clock[0]=10
        wait(lambda:len(stops)==1)
        wait(lambda:m.snapshot()['state']=='exited')
        assert thread.is_alive()
        release.set();thread.join(2)
        assert errors==[] and len(stops)==1
        assert m.snapshot()['stop_reason']==('execution_timeout' if limit=='business' else 'disconnection_timeout')
    finally:
        release.set();thread.join(3);m.close()


def test_unlimited_outage_keeps_running_and_persists_offline_exit(case):
    _,_,runtime,stops,clock=case
    with monitor(case) as m:
        clock[0]=100000
        wait(lambda:m.snapshot()['last_observed_at']==100000)
        assert stops==[] and m.snapshot()['state']=='running'
        runtime.update(state='exited',exit_code=0)
        wait(lambda:m.snapshot()['state']=='exited')
    with monitor(case) as recovered:
        assert recovered.snapshot()['exit_code']==0
        assert recovered.snapshot()['started_at']==0
        sent=[]
        def post(path,body):sent.append(body);return reply(body,'retain')
        recovered.exchange(SimpleNamespace(post=post))
        assert sent[0]['state']=='exited' and sent[0]['exit_code']==0
        assert sent[0]['elapsed_seconds']==100000
    assert stops==[]


def test_reconnected_cancel_stops_original_runtime(case):
    _,_,_,stops,_=case
    with monitor(case) as m:
        m.exchange(SimpleNamespace(post=lambda path,body:reply(body,'stop')))
        wait(lambda:len(stops)==1)
        wait(lambda:m.snapshot()['state']=='exited')
        assert m.snapshot()['control']['directive']=='stop'


def test_identity_conflict_never_stops_replacement(case):
    _,_,runtime,stops,clock=case
    with monitor(case) as m:
        runtime['identity']['runtime_id']='b'*64;clock[0]=1
        wait(lambda:m.snapshot()['blocked_reason'] is not None)
        assert not m.exchange(SimpleNamespace(post=lambda *a:pytest.fail('must not report another runtime')))
    assert stops==[]


def test_observation_failure_retries_and_retains_ownership(case,monkeypatch):
    _,_,runtime,stops,_=case;available=threading.Event()
    def inspect(self):
        if not available.is_set():raise OSError('runtime temporarily unavailable')
        return copy.deepcopy(runtime)
    monkeypatch.setattr(RecoveryRuntime,'inspect',inspect)
    with monitor(case) as m:
        wait(lambda:m.last_error is not None)
        assert isinstance(m.last_error,OSError) and stops==[]
        with pytest.raises(BlockingIOError):monitor(case)
        available.set();wait(lambda:m.last_error is None)


def test_close_during_http_retains_lock_until_response_finishes(case):
    entered=threading.Event();release=threading.Event();errors=[]
    m=monitor(case)
    def post(path,body):entered.set();assert release.wait(3);return reply(body)
    def exchange():
        try:m.exchange(SimpleNamespace(post=post))
        except Exception as exc:errors.append(exc)
    thread=threading.Thread(target=exchange);thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(RuntimeError,match='already in progress'):m.exchange(None)
        with pytest.raises(RuntimeError,match='ownership retained'):m.close()
        with pytest.raises(BlockingIOError):monitor(case)
        release.set();thread.join(2);assert errors==[]
        with pytest.raises(RuntimeError,match='closing'):m.exchange(None)
    finally:
        release.set();thread.join(3);m.close()
    with monitor(case):pass
    assert case[3]==[]


def test_blocked_runtime_inspection_cannot_release_ownership(case,monkeypatch):
    entered=threading.Event();release=threading.Event();runtime=case[2]
    def inspect(self):entered.set();assert release.wait(3);return copy.deepcopy(runtime)
    monkeypatch.setattr(RecoveryRuntime,'inspect',inspect)
    m=monitor(case)
    try:
        assert entered.wait(2)
        with pytest.raises(RuntimeError,match='observation has not stopped'):m.close(timeout=0)
        with pytest.raises(BlockingIOError):monitor(case)
    finally:release.set();m.close()


@pytest.mark.parametrize('interval',[0,-1,True,float('inf'),float('nan')])
def test_invalid_interval(case,interval):
    with pytest.raises(ValueError):RecoveryMonitor(case[0],case[1],interval=interval)


def test_wrong_boot_or_bound_identity_rejected(case):
    worker,launch,_,_,_=case
    changed=copy.deepcopy(launch);changed['identity']['runtime_id']='b'*64
    with pytest.raises(ValueError,match='original bound'):RecoveryMonitor(worker,changed)
    worker.state['recovery_assignment']['claim']['boot_id']='old'
    with pytest.raises(ValueError,match='original bound'):RecoveryMonitor(worker,launch)


def test_thread_start_failure_releases_supervisor_lock(case,monkeypatch):
    original=threading.Thread.start
    def fail(self):raise RuntimeError('no thread resources')
    monkeypatch.setattr(threading.Thread,'start',fail)
    with pytest.raises(RuntimeError,match='no thread'):monitor(case)
    monkeypatch.setattr(threading.Thread,'start',original)
    with monitor(case):pass


@pytest.mark.skipif(__import__('os').getenv('RECOVERY_RUNTIME_TEST')!='1',reason='real host/Docker monitor opt-in')
@pytest.mark.parametrize('mode',['host','docker'])
def test_real_runtime_stops_while_control_http_is_blocked(tmp_path,monkeypatch,mode):
    import json,os,uuid
    from backend.tests.test_host_backend import worker as fixture_worker
    from worker.client import Worker,docker
    from worker.host_backend import HostBackend
    worker=fixture_worker(tmp_path,['python3','business.py'],timeout=0)
    worker.host_mode=lambda:mode=='host';worker.slot_id='s';worker.start_lock=threading.Lock()
    task=copy.deepcopy(worker.state['task']);execution=task['execution_id'];name='mini-eval-'+execution
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    worker.state.update(created=False,recovery_assignment={'reconciliation_required':True,
        'claim':{'slot_id':'s','boot_id':boot,'requested_at':time.monotonic(),
        'receipt':{'request_id':str(uuid.uuid4()),'execution_id':execution,'generation':'g',
        'disposition':'reconcile','task':task,'policy':{'disconnect_tolerance_seconds':2}}}})
    host=HostBackend(worker);script='import time,signal,sys;signal.signal(signal.SIGTERM,lambda *args:sys.exit(0));print("running",flush=True);time.sleep(60)'
    def create(task):
        if mode=='host':
            (tmp_path/'business.py').write_text(script);host.create(task)
            path=host.root/'launch.json';launch=json.loads(path.read_text())
            launch['command']=['/usr/bin/python3',str(tmp_path/'business.py')];path.write_text(json.dumps(launch))
        else:
            docker('create','--name',name,'--label','mini-eval.execution='+execution,
                '--label','mini-eval.launch='+task['recovery_launch_id'],'--entrypoint','python',
                os.getenv('RECOVERY_RUNTIME_IMAGE','mini-eval-api:recovery-foundations-20261125'),'-c',script)
    worker.create=create;worker.name=lambda e:'mini-eval-'+e;worker.inspect=lambda e:Worker.inspect(worker,e)
    worker.api=SimpleNamespace(post=lambda path,body:{'execution_id':execution,**body,
        'action':'launch','reason':None,'duplicate':False,'valid_for_seconds':30})
    entered=threading.Event();release=threading.Event();stopped=threading.Event();errors=[]
    original_stop=RecoveryRuntime.stop
    def stop(self,identity):original_stop(self,identity);stopped.set()
    monkeypatch.setattr(RecoveryRuntime,'stop',stop)
    m=thread=None
    try:
        launch=Worker.start_recovery(worker)
        m=Worker.monitor_recovery(worker,launch)
        def post(path,body):
            entered.set();assert release.wait(10)
            return {'execution_id':execution,'generation':'g','sequence':body['sequence'],
                    'duplicate':False,'action':'track','reason':None}
        def exchange():
            try:m.exchange(SimpleNamespace(post=post))
            except Exception as exc:errors.append(exc)
        thread=threading.Thread(target=exchange);thread.start();assert entered.wait(2)
        assert stopped.wait(8)
        assert thread.is_alive()
        wait(lambda:m.snapshot()['state'] in ('exited','missing'))
        assert m.snapshot()['stop_reason']=='disconnection_timeout'
        assert RecoveryRuntime(worker,execution).inspect()['state']=='exited'
        # Recreate the adapter and startup handoff after systemd unloads the unit.
        assert Worker.start_recovery(worker)['identity']==launch['identity']
        release.set();thread.join(2);assert errors==[]
    finally:
        release.set()
        if thread:thread.join(3)
        if m:m.close()
        if mode=='host':host.remove(execution)
        else:docker('rm','--force',name,check=False)
