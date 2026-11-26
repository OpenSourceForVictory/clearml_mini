import json
import pytest
import threading
import time
from runner.log_capture import Ring
from worker.log_stream import Sender


def test_blocked_upload_does_not_block_control_and_pending_is_durable(tmp_path):
    entered=threading.Event();release=threading.Event()
    class API:
        def post(self,path,body):
            entered.set();release.wait(5)
            return {'accepted':True,'next_sequence':body['sequence']+1}
    ring=Ring(tmp_path/'logs');ring.write('stdout','hello')
    sender=Sender(API(),'execution',tmp_path/'logs',tmp_path/'journal.json')
    try:
        assert entered.wait(2)
        saved=json.loads((tmp_path/'journal.json').read_text())
        assert saved['pending']['content']=='hello'
        start=time.monotonic();sender.close()
        assert time.monotonic()-start<.5
        assert not sender.finish(.01)
    finally:
        release.set();sender.thread.join(2)
    assert json.loads((tmp_path/'journal.json').read_text())['offset']==0


def test_upload_coalesces_and_restores_pending(tmp_path):
    calls=[];done=threading.Event()
    class API:
        def post(self,path,body):
            calls.append(dict(body));done.set()
            return {'accepted':True,'next_sequence':body['sequence']+1}
    ring=Ring(tmp_path/'logs')
    for i in range(4):ring.write('stdout','hello'+str(i))
    journal=tmp_path/'journal.json'
    journal.write_text(json.dumps({'execution':'e','offset':0,'sequence':0,'pending':{'sequence':0,'content':'saved','stream':'stdout','gap':False},'pending_offset':1}))
    sender=Sender(API(),'e',tmp_path/'logs',journal)
    try:
        assert done.wait(2)
        deadline=time.monotonic()+2
        while sender.state['pending'] and time.monotonic()<deadline:time.sleep(.01)
        assert calls[0]['content']=='saved'
    finally:sender.close()
    sender.prepare()
    assert sender.state['pending']['content']=='hello1hello2hello3'


def test_recovered_connection_drains_final_tail_after_pending_gap(tmp_path):
    first = threading.Event()
    online = threading.Event()
    calls = []
    class API:
        def post(self, path, body):
            first.set()
            if not online.is_set():
                raise TimeoutError('temporary control outage')
            calls.append(dict(body))
            return {'next_sequence': body['sequence'] + 1}
    root = tmp_path / 'logs'
    ring = Ring(root)
    ring.write('system', 'bounded catch-up', gap=True)
    sender = Sender(API(), 'execution', root, tmp_path / 'journal.json')
    try:
        assert first.wait(2)
        ring.write('stdout', 'OFFLINE_STARTED\nOFFLINE_FINISHED\n')
        online.set()
        assert sender.finish(timeout=5)
        assert any('OFFLINE_STARTED' in row['content'] and 'OFFLINE_FINISHED' in row['content']
                   for row in calls)
        assert sender.state['pending'] is None
    finally:
        sender.close()


@pytest.mark.parametrize("cancel", [False, True])
def test_worker_heartbeats_survive_log_request_longer_than_lease(execution, monkeypatch, tmp_path, cancel):
    from worker import client
    from types import SimpleNamespace
    worker,task,events=execution
    (worker.workspace/'runs').mkdir(exist_ok=True)
    task.update(log_protocol=2,lease_seconds=3,benchmark='cs-client',
                client_spec={'service_mode':'none','template':{'execution_mode':'host','task_type':'command'}})
    root=tmp_path/'capture';ring=Ring(root/'logs');ring.write('stdout','start\n')
    entered=threading.Event();beats=[];started=time.monotonic()
    class Host:
        def __init__(self,w):self.root=root
    monkeypatch.setattr(client,'HostBackend',Host)
    monkeypatch.setattr(worker,'inspect',lambda e:{'State':{'Status':'running','Running':time.monotonic()-started<4.2,'ExitCode':0}})
    monkeypatch.setattr(worker,'upload_artifacts',lambda *a:None)
    original=worker.api.post
    def post(path,body=None):
        if path=='worker/heartbeat':beats.append(time.monotonic())
        return {**original(path,body),'lease_seconds':3,'cancel':cancel and len(beats)>=3}
    worker.api.post=post
    def blocked(self,path,body=None):
        entered.set();time.sleep(4)
        return {'accepted':True,'next_sequence':body['sequence']+1}
    monkeypatch.setattr(client.Client,'post',blocked)
    worker.execute(task)
    assert entered.is_set() and len(beats)>=5
    assert max(b-a for a,b in zip(beats,beats[1:]))<1.5
    complete=next(body for path,body in events if path.endswith('/complete'))
    assert complete['status']==('CANCELLED' if cancel else 'SUCCEEDED')
    assert any(path=='stop' for path,body in events)==cancel


from backend.tests.test_worker_execution_recovery import execution


@pytest.mark.parametrize('stdout_bytes', [100000, 4000000])
def test_final_drain_preserves_stderr_before_buffered_stdout(tmp_path, stdout_bytes):
    # Reproduce stderr becoming readable while a large stdout pipe still drains.
    ring=Ring(tmp_path/'logs');ring.write('stderr','critical tail error')
    ring.write('stdout','x'*stdout_bytes)
    sender=Sender.__new__(Sender)
    sender.root=tmp_path/'logs';sender.journal=tmp_path/'journal.json'
    sender.state={'offset':0,'sequence':0,'pending':None}
    sender.finishing=threading.Event();sender.finishing.set()
    assert sender.prepare()
    body=sender.state['pending']
    assert 'critical tail error' in body['content'] and body['gap']
    assert len(body['content'].encode())<=16384


def test_recovered_legacy_journal_does_not_switch_log_protocol(execution,monkeypatch):
    from worker import log_stream
    worker,task,events=execution
    old={**task,'client_spec':{'template':{'task_type':'command'}}}
    worker.state={'task':old,'created':True,'offset':0,'sequence':0,'pending':None}
    monkeypatch.setattr(log_stream,'Sender',lambda *a:(_ for _ in ()).throw(AssertionError('legacy switched')))
    worker.execute({**old,'log_protocol':2})
    assert next(body for path,body in events if path.endswith('/complete'))['status']=='SUCCEEDED'


def test_container_stderr_snapshot_survives_missing_docker_history(tmp_path):
    sender=Sender.__new__(Sender)
    sender.root=tmp_path/'missing-ring';sender.journal=tmp_path/'journal.json'
    sender.stderr_source=tmp_path/'stderr-tail.json'
    sender.stderr_source.write_text(json.dumps({'content':'fatal before Docker attachment'}))
    sender.state={'offset':0,'sequence':0,'pending':None}
    sender.finishing=threading.Event();sender.finishing.set()
    assert sender.prepare()
    assert sender.state['pending']['gap']
    assert 'fatal before Docker attachment' in sender.state['pending']['content']
    sender.state['pending']=None
    assert not sender.prepare()


def test_next_execution_does_not_reuse_previous_log_cursor(tmp_path):
    journal=tmp_path/'journal.json'
    journal.write_text(json.dumps({'execution':'old','offset':100,'sequence':9,'pending':None}))
    Ring(tmp_path/'logs').write('stdout','new execution')
    sent=threading.Event();calls=[]
    class API:
        def post(self,path,body):
            calls.append((path,dict(body)));sent.set()
            return {'next_sequence':1}
    sender=Sender(API(),'new',tmp_path/'logs',journal)
    try:
        assert sent.wait(2)
        assert calls==[('worker/executions/new/logs-v2',{'sequence':0,'content':'new execution','stream':'stdout','gap':False})]
    finally:sender.close()


def test_docker_collector_reconnect_is_explicit(tmp_path,monkeypatch):
    import subprocess,sys
    from worker import log_stream
    from runner.log_capture import read_ring
    original=subprocess.Popen;calls=[]
    def process(argv,**kwargs):
        calls.append(argv)
        return original([sys.executable,'-c',"print('line');raise SystemExit("+str(1 if len(calls)==1 else 0)+")"],**kwargs)
    monkeypatch.setattr(log_stream.subprocess,'Popen',process)
    collector=log_stream.DockerCollector('owned',tmp_path)
    try:
        collector.thread.join(5)
        assert not collector.thread.is_alive()
        assert len(calls)==2 and all('--tail' in argv for argv in calls)
        rows=[];cursor=0
        while True:
            row,cursor=read_ring(tmp_path,cursor)
            if row is None:break
            rows.append(row)
        assert any('restarted' in row['content'] and row['gap'] for row in rows)
    finally:collector.close()


def test_protocol_event_cursor_budget_and_partial_line(tmp_path):
    from worker.client import Worker
    from types import SimpleNamespace
    calls=[];saved=[]
    worker=SimpleNamespace(state={},api=SimpleNamespace(post=lambda path,body:calls.append(body)))
    worker.save=lambda:saved.append(dict(worker.state))
    path=tmp_path/'events.jsonl'
    assert Worker.send_protocol_events(worker,'e',path)
    path.write_text(''.join(json.dumps({'episode_id':i})+'\n' for i in range(18))+'{"partial":')
    assert not Worker.send_protocol_events(worker,'e',path)
    assert len(calls)==16
    assert not Worker.send_protocol_events(worker,'e',path)
    assert len(calls)==18 and len(saved)==18
    offset=worker.state['protocol_event_offset']
    with path.open('a') as f:f.write('true}\n')
    assert Worker.send_protocol_events(worker,'e',path)
    assert calls[-1]=={'partial':True} and worker.state['protocol_event_offset']>offset


def test_protocol_event_failed_upload_keeps_cursor(tmp_path):
    from worker.client import Worker
    from types import SimpleNamespace
    path=tmp_path/'events.jsonl';path.write_text('{"episode_id":1}\n')
    def fail(*args):raise TimeoutError('unavailable')
    worker=SimpleNamespace(state={},api=SimpleNamespace(post=fail),save=lambda:None)
    with pytest.raises(TimeoutError):Worker.send_protocol_events(worker,'e',path)
    assert 'protocol_event_offset' not in worker.state


def test_sender_stops_on_auth_rejection_without_acknowledging(tmp_path):
    class Rejected(Exception):status=401
    class API:
        def post(self,*args):raise Rejected()
    Ring(tmp_path/'logs').write('stdout','pending')
    sender=Sender(API(),'e',tmp_path/'logs',tmp_path/'journal.json')
    try:
        sender.thread.join(2)
        assert not sender.thread.is_alive()
        assert sender.state['offset']==0 and sender.state['pending']['content']=='pending'
        assert not sender.finish(.01)
    finally:sender.close()


@pytest.mark.parametrize('failure',['runtime','rejected','cancel'])
def test_exception_completion_explicitly_marks_unflushed_logs(execution,monkeypatch,failure):
    from worker.client import APIError,StartupCancelled
    worker,task,events=execution
    task['log_protocol']=2
    error={'runtime':RuntimeError('lost process'),'rejected':APIError(400,'invalid event'),'cancel':StartupCancelled()}[failure]
    def fail(*args):raise error
    monkeypatch.setattr(worker,'inspect',fail)
    worker.execute(task)
    body=next(body for path,body in events if path.endswith('/complete'))
    assert body['log_gap'] and body['status']==('CANCELLED' if failure=='cancel' else 'FAILED')


def test_execution_log_journal_is_not_a_recoverable_execution(tmp_path):
    import uuid
    from worker.client import Worker
    parent=Worker({},tmp_path/'state')
    identity=str(uuid.uuid4());child=parent.child(identity)
    child.state={'task':{'execution_id':identity},'offset':0,'sequence':0};child.save()
    ring=Ring(tmp_path/'logs');ring.write('stdout','tail')
    class API:
        def post(self,path,body):return {'next_sequence':1}
    sender=Sender(API(),identity,tmp_path/'logs',str(child.path)+'.logs')
    try:
        sender.thread.join(.1)
    finally:sender.close()
    recovered=Worker({},tmp_path/'state');recovered.recover_journals()
    assert set(recovered.children)=={identity}


@pytest.mark.parametrize('count', [0, 1, 32, 33, 300])
def test_upload_only_latest_diagnostic_window(tmp_path, count):
    from worker.log_stream import UPLOAD_TAIL_RECORDS
    ring = Ring(tmp_path/'logs')
    for index in range(count):
        ring.write('stdout', f'{index:04d}:'+'x'*2043)
    sender = Sender.__new__(Sender)
    sender.root = tmp_path/'logs'
    sender.journal = tmp_path/'journal.json'
    sender.finishing = threading.Event()
    sender.state = {'offset':0, 'sequence':0, 'pending':None}
    bodies = []
    while sender.prepare():
        bodies.append(sender.state['pending'])
        sender.state['offset'] = sender.state.pop('pending_offset')
        sender.state['sequence'] += 1
        sender.state['pending'] = None
    content = ''.join(body['content'] for body in bodies)
    first = max(0, count-UPLOAD_TAIL_RECORDS)
    assert content == ''.join(f'{i:04d}:'+'x'*2043 for i in range(first, count))
    assert sum(len(body['content'].encode()) for body in bodies) <= 65536
    assert all(len(body['content'].encode()) <= 16384 for body in bodies)
    assert (any(body['gap'] for body in bodies)) == (count>32)
    assert sender.state['offset'] == count


def test_reconnect_replays_pending_then_skips_old_logs_without_touching_results(tmp_path):
    from runner.log_capture import ProtocolCapture
    root = tmp_path/'logs'
    ring = Ring(root)
    ring.write('stdout', 'previous request')
    event = b'MINI_EVAL_EVENT {"episode_id":1,"status":"SUCCESS"}\n'
    protocol = ProtocolCapture(tmp_path/'protocol')
    protocol.feed(event)
    original = protocol.event_path.read_bytes()
    for index in range(300):
        ring.write('stdout', f'new-{index}\n')
    pending = {'sequence':7, 'content':'previous request', 'stream':'stdout', 'gap':False}
    journal = tmp_path/'journal.json'
    journal.write_text(json.dumps({'execution':'e','offset':0,'sequence':7,
                                  'pending':pending,'pending_offset':1}))
    calls=[]; uploaded=threading.Event()
    class API:
        def post(self, path, body):
            calls.append(dict(body))
            if len(calls)==2: uploaded.set()
            return {'accepted':True,'next_sequence':body['sequence']+1}
    sender=Sender(API(),'e',root,journal)
    try:
        assert uploaded.wait(5)
        assert calls[0]==pending
        assert calls[1]['sequence']==8 and calls[1]['gap']
        assert calls[1]['content']==''.join(f'new-{i}\n' for i in range(268,300))
        assert protocol.event_path.read_bytes()==original
    finally:
        sender.close()
