"""Resource source negotiation and slow dependency isolation contracts."""
import copy
import threading
import time
import uuid
from types import SimpleNamespace
import pytest
from backend.tests.test_execution import env
from backend.tests.test_gpu_sharing import setup_gpu, submit_gpu
from backend.app.core import Slot, Worker
from backend.app.scheduling import tick
from backend.tests.test_agent_modules import API, FakePrograms


def sample(values, age=0):
    return {'status':'ok','sampled_at':time.time()-age,'values':values}


def register(c, token, protocol=1):
    r=c.post('/api/v1/agent/register',headers=token,json={'boot_id':uuid.uuid4().hex,'version':'1.1.0','resource_protocol':protocol})
    assert r.status_code==200,r.text
    return r.json()['session']


def beat(c, token, session, seq=1, samples=None):
    r=c.post('/api/v1/agent/heartbeat',headers=token,json={'session':session,'sequence':seq,'applied_revision':0,'config_status':'applied','samples':samples or {}})
    assert r.status_code==200,r.text
    return r


def samples():
    return {'host':sample({'cpu_count':8,'memory_total_mb':32768,'memory_available_mb':20000}),
            'gpu':sample({'gpu_0_uuid':'GPU-0','gpu_0_total_mb':49152,'gpu_0_free_mb':40000,'gpu_0_utilization':10})}


def test_agent_source_identity_and_legacy_rollback(env):
    main,c,_=env;pool,slots,tokens=setup_gpu(env,1);token=tokens[0]
    session=register(c,token);values=samples();beat(c,token,session,samples=values)
    def check(free):
        with main.SessionLocal() as s: assert s.get(Slot,slots[0]).free_vram_mb==free
    check(40000)
    r=c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slots[0],'telemetry_failed':True})
    assert r.json()['resource_source']=='agent';check(40000)
    values['gpu']['values']['gpu_0_uuid']='wrong';beat(c,token,session,2,values);check(None)
    beat(c,token,session,1,samples());check(None)  # old sequence cannot restore data
    register(c,token,0)
    r=c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slots[0],'telemetry':{'free_vram_mb':30000,'gpu_utilization':0}})
    assert r.json()['resource_source']=='worker';check(30000)


@pytest.mark.parametrize('fault',['memory','cpu','stale','failed','future','missing'])
def test_host_resource_failures_stop_admission(env,fault):
    main,c,_=env;pool,slots,tokens=setup_gpu(env,1);token=tokens[0]
    session=register(c,token);values=samples()
    if fault=='memory':values['host']['values']['memory_available_mb']=512
    if fault=='cpu':values['host']['values']['cpu_count']=0
    if fault=='stale':values['host']['sampled_at']-=20
    if fault=='future':values['host']['sampled_at']+=60
    if fault=='failed':values['host']['status']='failed'
    if fault=='missing':values.pop('host')
    beat(c,token,session,samples=values);submit_gpu(env,pool)
    assert tick(main.SessionLocal)==0
    beat(c,token,session,2,samples());assert tick(main.SessionLocal)==1
    task=c.post('/api/v1/worker/slots/'+slots[0]+'/claim',headers=token).json()['task']
    assert task
    beat(c,token,session,3,{})
    # Lost telemetry prevents admission, not renewal of a running execution lease.
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slots[0],'execution_id':task['execution_id']}).status_code==200


def test_sample_age_is_not_renewed_by_repeated_heartbeats(env):
    main,c,_=env;_,slots,tokens=setup_gpu(env,1);token=tokens[0]
    session=register(c,token);values=samples();values['gpu']['sampled_at']-=10
    beat(c,token,session,samples=values)
    with main.SessionLocal() as s:first=s.get(Slot,slots[0]).telemetry_at
    beat(c,token,session,2,values)
    with main.SessionLocal() as s:assert s.get(Slot,slots[0]).telemetry_at==first
    values['gpu']['sampled_at']-=10;beat(c,token,session,3,values)
    with main.SessionLocal() as s:assert s.get(Slot,slots[0]).telemetry_at is None


def test_slow_state_ack_does_not_block_agent_heartbeats(tmp_path,monkeypatch):
    from node_agent.client import Agent
    from node_agent.monitor import Monitor
    from node_agent import state
    api=API();agent=Agent({'state_dir':str(tmp_path/'state')},transport=api,programs=FakePrograms(),monitor=Monitor(tmp_path,{'host':lambda:{}}))
    gate=threading.Event();entered=threading.Event();original=state.atomic_json
    original_post=api.post
    def post(path,body):
        r=original_post(path,body)
        if path=='heartbeat':r['event_ack']=1
        return r
    api.post=post
    def slow(*a):entered.set();gate.wait(3);return original(*a)
    monkeypatch.setattr(state,'atomic_json',slow)
    try:
        agent.step();assert entered.wait(1)
        start=time.monotonic()
        for _ in range(10):agent.step()
        assert time.monotonic()-start<.5
        assert agent.state.value['revision']==0
    finally:gate.set();agent.controller.join(2);agent.state.close()


def test_transport_has_bounded_deadline_and_single_inflight():
    from node_agent.transport import Transport
    t=Transport('http://local','token');t.deadline=.03
    gate=threading.Event();calls=[]
    def slow(*a):calls.append(1);gate.wait(2);return {}
    t._post=slow
    try:
        with pytest.raises(TimeoutError):t.post('heartbeat',{})
        for _ in range(10):
            with pytest.raises(TimeoutError):t.post('heartbeat',{})
        assert len(calls)==1
    finally:gate.set();assert t.pending['heartbeat'].wait(1)


def control_worker():
    storage=SimpleNamespace(snapshot=lambda:{'revision':0,'config':{'workspace_root':'/tmp','min_free_disk_gb':0}},report=lambda:{'status':'applied'})
    runtime=SimpleNamespace(report=lambda:{},snapshot=lambda:{'config':{'images':[]}})
    return SimpleNamespace(children={},config={},storage=storage,runtime=runtime,host_environments=SimpleNamespace(report=lambda:{}),slot_id='slot')


def test_hung_storage_never_blocks_control_cache_or_spawns_more_threads():
    from worker.control import Control
    gate=threading.Event();entered=threading.Event();w=control_worker()
    def slow():entered.set();gate.wait(2);return {}
    w.storage.report=slow
    control=Control(w,None,lambda c:{})
    try:
        control.refresh();assert entered.wait(1);thread=control.thread
        start=time.monotonic()
        for _ in range(100):
            control.refresh();assert control.report()['disk']['free_mb']==0
            assert not control.ready();assert control.thread is thread
        assert time.monotonic()-start<.2
    finally:gate.set();control.thread.join(2)


def test_agent_mode_skips_periodic_gpu_probe_and_stale_cache_closes_admission():
    from worker.control import Control
    calls=[];control=Control(control_worker(),None,lambda c:calls.append(1) or {'free_vram_mb':1})
    control.source='agent';control.refresh();control.thread.join(1)
    assert control.ready() and calls==[] and 'telemetry' not in control.report()
    control.sampled-=11
    assert not control.ready() and control.report()['disk']['free_mb']==0
    control.source='worker';control.refresh();control.thread.join(1)
    assert calls==[1] and control.report()['telemetry']['free_vram_mb']==1


def test_one_agent_updates_multiple_gpu_slots_by_uuid(env):
    main,c,_=env;pool,slots,tokens=setup_gpu(env,1);token=tokens[0]
    r=c.post('/api/v1/worker/register',headers=token,json={'pool_id':pool,'gpu_index':1,'gpu_uuid':'GPU-second','gpu_model':'RTX4090','vram_gb':24,'protocol_version':2})
    assert r.status_code==200,r.text
    second=r.json()['slot_id'];session=register(c,token);values=samples()
    values['gpu']['values'].update(gpu_1_uuid='GPU-second',gpu_1_total_mb=24576,gpu_1_free_mb=12000,gpu_1_utilization=25)
    beat(c,token,session,samples=values)
    with main.SessionLocal() as s:
        assert s.get(Slot,slots[0]).free_vram_mb==40000
        assert s.get(Slot,second).free_vram_mb==12000
    values['gpu']['values']['gpu_1_uuid']='GPU-0'
    beat(c,token,session,2,values)
    with main.SessionLocal() as s:
        assert s.get(Slot,slots[0]).free_vram_mb==40000
        assert s.get(Slot,second).free_vram_mb is None


def test_idle_event_ack_does_not_write(tmp_path,monkeypatch):
    from node_agent.state import State
    from node_agent import state as mod
    state=State(tmp_path/'state')
    def forbidden(*a):raise AssertionError('idle fsync')
    monkeypatch.setattr(mod,'atomic_json',forbidden)
    try:
        for _ in range(100):state.acknowledge(0)
    finally:state.close()


def test_sample_timestamp_marks_completion(tmp_path):
    from node_agent.monitor import Monitor
    gate=threading.Event();m=Monitor(tmp_path,{'gpu':lambda:gate.wait(1) or {}})
    m.sample(5);before=time.time();gate.set();m.threads['gpu'].join(1)
    assert m.samples['gpu']['sampled_at']>=before


def test_cpu_sampler_uses_counter_deltas_without_sleep(monkeypatch):
    from pathlib import Path
    from node_agent.monitor import HostProbe
    original=Path.read_text;counter=['cpu 10 0 10 80 0 0 0 0\n']
    def read(p,*a,**kw):return counter[0] if str(p)=='/proc/stat' else original(p,*a,**kw)
    monkeypatch.setattr(Path,'read_text',read)
    probe=HostProbe();assert 'cpu_utilization' not in probe()
    counter[0]='cpu 20 0 20 160 0 0 0 0\n'
    assert probe()['cpu_utilization']==pytest.approx(20)


def test_late_control_result_cannot_reopen_admission():
    from worker.control import Control
    w=control_worker();gate=threading.Event();entered=threading.Event()
    def slow():entered.set();gate.wait(2);return {}
    w.storage.report=slow;control=Control(w,None,lambda c:{})
    try:
        control.refresh();assert entered.wait(1);control.started-=11
    finally:gate.set();control.thread.join(1)
    assert control.result is None and not control.ready()


def test_cpu_only_agent_has_no_nvidia_probe(tmp_path,monkeypatch):
    from node_agent import monitor
    monkeypatch.setattr(monitor.shutil,'which',lambda name:None)
    m=monitor.Monitor(tmp_path)
    assert 'host' in m.probes and 'gpu' not in m.probes and 'npu' not in m.probes


def ascend_sample():
    from pathlib import Path
    from node_agent.ascend import mappings, metrics
    root=Path(__file__).parent/'fixtures'
    return metrics((root/'ascend_910b3_metrics.txt').read_text(),mappings((root/'ascend_910b3_mapping.txt').read_text()))


def test_ascend_inventory_is_not_a_cuda_device():
    from node_agent.devices import inventory
    devices=inventory({'npu':sample(ascend_sample())})
    assert len(devices)==8
    assert all(d['kind']=='npu' and d['vendor']=='huawei' and not d['execution_ready'] for d in devices)
    assert all(d['memory_total_mb']==65536 and d['memory_free_mb']>0 for d in devices)
    assert {d['index'] for d in devices}==set(range(8))
    assert all(d['chip_id']==0 and d['status']=='ok' for d in devices)


def test_mixed_vendor_inventory_preserved_without_admitting_npu_as_gpu(env):
    from node_agent.devices import inventory
    main,c,a=env;_,slots,tokens=setup_gpu(env,1);token=tokens[0];session=register(c,token)
    values=samples();values['npu']=sample(ascend_sample())
    devices=inventory(values)
    r=c.post('/api/v1/agent/heartbeat',headers=token,json={'session':session,'sequence':1,'applied_revision':0,'config_status':'applied','samples':values,'devices':devices})
    assert r.status_code==200,r.text
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0]);w=s.get(Worker,slot.worker_id)
        stored=w.connection['agent']['devices']
        assert {d['kind'] for d in stored}=={'gpu','npu'}
        npu=next(d for d in stored if d['kind']=='npu')
        assert npu['memory_total_mb']==65536 and not npu['execution_ready']
        assert slot.free_vram_mb==40000


def test_host_memory_accounts_for_external_use_and_all_platform_reservations():
    from backend.app.node_agents import host_codes
    from backend.app.core import now
    values=samples()
    values['host']['values']['memory_available_mb']=2048
    worker=SimpleNamespace(connection={'agent':{'resource_protocol':1,'seen_at':now().isoformat(),'samples':values}})
    pool=SimpleNamespace(cpu_per_job=1,ram_gb_per_job=1)
    assert host_codes(worker,pool,1,4,pending_ram=0)==['node_ram']
    assert host_codes(worker,pool,1,4,pending_ram=2)==['node_ram']
    assert host_codes(worker,pool,1,32,pending_ram=0)==['node_ram']
    values['host']['values']['memory_available_mb']=8192
    assert host_codes(worker,pool,1,4,pending_ram=0)==[]


def test_worker_supervisor_heartbeats_while_storage_probe_is_blocked(tmp_path,monkeypatch):
    from worker import client
    from backend.tests.test_worker_storage import settings
    config={**settings(tmp_path),'mock':True,'node_name':'isolated','api_url':'http://unused','token':'unused'}
    w=client.Worker(config,tmp_path/'state.json')
    gate=threading.Event();entered=threading.Event();calls=[]
    def blocked():entered.set();gate.wait(3);return {}
    w.storage.report=blocked
    class Stop(BaseException):pass
    class API:
        def post(self,path,body):
            calls.append((path,body))
            if path=='worker/register':return {'slot_id':str(uuid.uuid4()),'resource_source':'agent'}
            assert path=='worker/heartbeat'  # no claim while storage is unknown
            if len(calls)>=4:raise Stop()
            return {'resource_source':'agent'}
    class Retention:
        def __init__(self,*a):pass
        def start(self):pass
        def close(self):pass
    monkeypatch.setattr(client,'Retention',Retention)
    sleep=time.sleep;monkeypatch.setattr(client.time,'sleep',lambda n:sleep(.01))
    w.api=API()
    try:
        with pytest.raises(Stop):w.run()
        assert entered.is_set()
        assert len([p for p,b in calls if p=='worker/heartbeat'])==3
        assert all(b['disk']['free_mb']==0 for p,b in calls if p=='worker/heartbeat')
    finally:
        gate.set();w.control.thread.join(2);w.shutdown();w.slot_lock.close()


@pytest.mark.parametrize('fault',['missing_hbm','missing_card','bad_total','bad_mapping'])
def test_ascend_unknown_or_incomplete_layout_fails_closed(fault):
    from pathlib import Path
    from node_agent.ascend import mappings, metrics
    root=Path(__file__).parent/'fixtures'
    mapping=mappings((root/'ascend_910b3_mapping.txt').read_text())
    text=(root/'ascend_910b3_metrics.txt').read_text()
    if fault=='missing_hbm':text=text.replace('HBM-Usage(MB)','Other-Memory(MB)')
    if fault=='missing_card':text='\n'.join(line for line in text.splitlines() if '0000:C1:00.0' not in line)
    if fault=='bad_total':text=text.replace('65536','1')
    if fault=='bad_mapping':mapping.pop((0,0))
    with pytest.raises(ValueError):metrics(text,mapping)


def test_ascend_uses_one_batch_query_after_cached_discovery():
    from pathlib import Path
    from node_agent.ascend import AscendProbe
    root=Path(__file__).parent/'fixtures';calls=[]
    def command(argv):
        calls.append(argv)
        return (root/('ascend_910b3_mapping.txt' if '-m' in argv else 'ascend_910b3_metrics.txt')).read_text()
    probe=AscendProbe(command);assert probe()==probe()
    assert calls==[['npu-smi','info','-m'],['npu-smi','info'],['npu-smi','info']]
