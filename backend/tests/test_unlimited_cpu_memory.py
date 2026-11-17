"""Pool zero budgets reserve no CPU/RAM and omit runtime quotas."""
from types import SimpleNamespace
from datetime import timedelta

import pytest

from backend.tests.test_execution import env
from backend.tests.test_gpu_sharing import setup_gpu, submit_gpu
from backend.app.core import Pool, Run, Slot, Worker
from backend.app.core import now
from backend.app.job_resources import budget, resolve, validate
from backend.app.node_resources import host_codes
from backend.app.scheduling import node_codes, tick
from sqlalchemy import select


def test_unlimited_pool_schedules_without_cpu_ram_reservations(env, monkeypatch):
    main,c,auth=env
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL','10')
    monkeypatch.setenv('MAX_ACTIVE_PER_USER','10')
    pool,slots,tokens=setup_gpu(env,1)
    response=c.patch(f'/api/v1/admin/pools/{pool}',headers=auth['admin'],json={'cpu_per_job':0,'ram_gb_per_job':0})
    assert response.status_code==200,response.text
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0])
        worker=s.get(Worker,slot.worker_id)
        worker.cpu_capacity=worker.ram_gb_capacity=1
        s.commit()
    jobs=[submit_gpu(env,pool) for _ in range(2)]
    assert tick(main.SessionLocal)==2
    with main.SessionLocal() as s:
        runs=list(s.scalars(select(Run).where(Run.job_id.in_(jobs))))
        assert len(runs)==2 and all(r.reserved_cpu==r.reserved_ram_gb==0 for r in runs)
        assert s.get(Pool,pool).cpu_per_job==s.get(Pool,pool).ram_gb_per_job==0
    task=c.post(f'/api/v1/worker/slots/{slots[0]}/claim',headers=tokens[0]).json()['task']
    assert task['cpus']==task['ram_gb']==0


def test_create_pool_accepts_independent_zero_defaults(env):
    _,c,auth=env
    for cpu,memory in ((0,2),(2,0),(0,0)):
        response=c.post('/api/v1/admin/pools',headers=auth['admin'],json={
            'name':f'zero-{cpu}-{memory}','gpu_model':'virtual','cpu_per_job':cpu,'ram_gb_per_job':memory})
        assert response.status_code==201,response.text
        created=response.json()
        assert created['cpu_per_job']==cpu and created['ram_gb_per_job']==memory


def test_unlimited_pool_still_honors_explicit_positive_requests():
    pool=SimpleNamespace(cpu_per_job=0,ram_gb_per_job=0,gpu_model='*',allow_shared=False)
    request=validate(pool,{'cpus':8,'ram_gb':64,'gpu_mode':'exclusive','requested_vram_gb':0})
    assert request['cpus']==8 and request['ram_gb']==64
    job=SimpleNamespace(requested_cpu=8,requested_ram_gb=64)
    assert budget(pool,job).cpu_per_job==8
    worker=SimpleNamespace(connection={},max_concurrent=2,cpu_capacity=4,ram_gb_capacity=8)
    assert node_codes(worker,budget(pool,job),0,0,0)==['node_cpu','node_ram']


def test_template_request_above_pool_default_uses_node_capacity():
    pool=SimpleNamespace(cpu_per_job=1,ram_gb_per_job=2,gpu_model='*',allow_shared=False)
    template={'resource_requirements':{'version':1,'cpu':{'minimum':1,'default':8},
              'memory':{'minimum':1,'default':16},'accelerator':{'type':'gpu','mode':'exclusive',
              'memory':{'minimum':0,'default':0}},'allow_overrides':[]}}
    request,sources=resolve(pool,template)
    assert request['cpus']==8 and request['ram_gb']==16
    assert sources['cpus']==sources['ram_gb']=='template'
    worker=SimpleNamespace(connection={},max_concurrent=2,cpu_capacity=8,ram_gb_capacity=16)
    job=SimpleNamespace(requested_cpu=request['cpus'],requested_ram_gb=request['ram_gb'])
    assert node_codes(worker,budget(pool,job),0,0,0)==[]
    worker.ram_gb_capacity=15
    assert node_codes(worker,budget(pool,job),0,0,0)==['node_ram']


def test_unlimited_axes_skip_only_their_own_host_capacity_checks():
    worker=SimpleNamespace(connection={'agent':{'resource_protocol':1}})
    both=SimpleNamespace(cpu_per_job=0,ram_gb_per_job=0)
    assert host_codes(worker,both,0,0)==[]
    assert node_codes(SimpleNamespace(max_concurrent=2,cpu_capacity=1,ram_gb_capacity=1),both,0,0,0)==[]


@pytest.mark.parametrize('cpu_request,ram_request,expected', [
    (0,0,[]),
    (1,0,['node_telemetry_stale']),
    (0,1,['node_telemetry_stale']),
])
def test_stale_host_sample_only_blocks_requested_axes(cpu_request,ram_request,expected):
    worker=SimpleNamespace(connection={'agent':{'resource_protocol':1,'seen_at':now().isoformat(),
           'samples':{'host':{'status':'ok','sampled_at':(now()-timedelta(seconds=60)).timestamp(),
                               'values':{'cpu_count':8,'memory_total_mb':8192,'memory_available_mb':8192}}}}})
    request=SimpleNamespace(cpu_per_job=cpu_request,ram_gb_per_job=ram_request)
    assert host_codes(worker,request,0,0)==expected


@pytest.mark.parametrize('values,claim_values,expected', [
    ({'cpu_count':4,'memory_total_mb':8192,'memory_available_mb':8192},(5,0),['node_cpu']),
    ({'cpu_count':4,'memory_total_mb':8192,'memory_available_mb':2048},(0,3),['node_ram']),
    ({'cpu_count':4,'memory_total_mb':8192,'memory_available_mb':8192},(0,3),[]),
    ({'memory_total_mb':8192,'memory_available_mb':8192},(0,3),[]),
    ({'cpu_count':4},(1,0),[]),
])
def test_host_sample_checks_only_requested_capacity(values,claim_values,expected):
    worker=SimpleNamespace(connection={'agent':{'resource_protocol':1,'seen_at':now().isoformat(),
           'samples':{'host':{'status':'ok','sampled_at':now().timestamp(),'values':values}}}})
    claim=SimpleNamespace(cpu_per_job=claim_values[0],ram_gb_per_job=claim_values[1])
    assert host_codes(worker,claim,0,0)==expected
