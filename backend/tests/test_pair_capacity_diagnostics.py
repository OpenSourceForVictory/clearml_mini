from types import SimpleNamespace as NS

import pytest

from backend.app.orchestration_scheduler import Planner


def scenario(*, same_slot=False, same_node=False):
    planner=Planner.__new__(Planner)
    planner.pools={
        'client':NS(id='client',cpu_per_job=2,ram_gb_per_job=4,vram_reserve_gb=0,max_tasks_per_gpu=2),
        'server':NS(id='server',cpu_per_job=2,ram_gb_per_job=4,vram_reserve_gb=0,max_tasks_per_gpu=2),
    }
    planner.workers={'s':NS(roles=['server','client'] if same_node or same_slot else ['server'],cpu_capacity=8,ram_gb_capacity=16,max_concurrent=2)}
    if not same_node:
        planner.workers['c']=NS(roles=['client'],cpu_capacity=8,ram_gb_capacity=16,max_concurrent=2)
    server_slot=NS(id='s1',worker_id='s',pool_id='server',gpu_model='A',vram_mb=24*1024)
    client_slot=server_slot if same_slot else NS(id='c1',worker_id='s' if same_node else 'c',pool_id='client',gpu_model='A',vram_mb=24*1024)
    if same_slot:planner.pools['client']=planner.pools['server']
    planner.slots=[server_slot] if same_slot else [server_slot,client_slot]
    client=NS(owner_id='u',pool_id='server' if same_slot else 'client',benchmark='cs-client',gpu_mode='exclusive',requested_vram_gb=0,required_gpu_model=None,requested_cpu=None,requested_ram_gb=None)
    definition={'pool_id':'server','resources':{'gpu_mode':'exclusive','requested_vram_gb':0,'required_gpu_model':None},'template':{}}
    return planner,client,definition


def test_pair_capacity_separate_nodes_and_missing_role_are_queueable():
    planner,client,definition=scenario()
    assert planner.structural_pair_diagnosis(client,definition)==(True,[])
    assert planner.structural_pair(client,definition) is True
    planner.workers['s'].roles=['client']
    assert planner.structural_pair_diagnosis(client,definition)==(None,[])
    assert planner.structural_pair(client,definition) is None


@pytest.mark.parametrize('change,reason',[
    (lambda p,c,d:setattr(c,'required_gpu_model','B'),'客户端设备型号不匹配'),
    (lambda p,c,d:setattr(p.workers['c'],'cpu_capacity',1),'客户端单项 CPU 超过节点容量'),
    (lambda p,c,d:setattr(p.workers['c'],'ram_gb_capacity',1),'客户端单项内存超过节点容量'),
    (lambda p,c,d:setattr(c,'requested_vram_gb',25),'客户端单项设备内存超过卡容量'),
])
def test_pair_capacity_single_side_reason(change,reason):
    planner,client,definition=scenario()
    change(planner,client,definition)
    assert planner.structural_pair_diagnosis(client,definition)==(False,[reason])
    assert planner.structural_pair(client,definition) is False


def test_pair_capacity_same_card_sharing_and_vram():
    planner,client,definition=scenario(same_slot=True)
    assert planner.structural_pair_diagnosis(client,definition)==(False,['同卡不能同时容纳独占请求'])
    client.gpu_mode=definition['resources']['gpu_mode']='shared'
    client.requested_vram_gb=definition['resources']['requested_vram_gb']=8
    assert planner.structural_pair_diagnosis(client,definition)==(True,[])
    planner.pools['server'].max_tasks_per_gpu=1
    assert planner.structural_pair_diagnosis(client,definition)==(False,['同卡共享任务上限小于 2'])
    planner.pools['server'].max_tasks_per_gpu=2
    definition['resources']['requested_vram_gb']=17
    assert planner.structural_pair_diagnosis(client,definition)==(False,['同卡双方设备内存申请之和超过容量'])


@pytest.mark.parametrize('field,value,reason',[
    ('max_concurrent',1,'同机并发上限小于 2'),
    ('cpu_capacity',3,'同机双方 CPU 申请之和超过节点容量'),
    ('ram_gb_capacity',7,'同机双方内存申请之和超过节点容量'),
])
def test_pair_capacity_same_node_joint_budget(field,value,reason):
    planner,client,definition=scenario(same_node=True)
    setattr(planner.workers['s'],field,value)
    assert planner.structural_pair_diagnosis(client,definition)==(False,[reason])


def test_same_node_pair_uses_requested_resources_not_pool_defaults_as_ceiling():
    planner,client,definition=scenario(same_node=True)
    for pool in planner.pools.values():
        pool.cpu_per_job=pool.ram_gb_per_job=0
    planner.workers['s'].cpu_capacity=planner.workers['s'].ram_gb_capacity=1
    assert planner.structural_pair_diagnosis(client,definition)==(True,[])
    client.requested_cpu=2
    assert planner.structural_pair_diagnosis(client,definition)==(False,['客户端单项 CPU 超过节点容量'])


@pytest.mark.parametrize('client_cpu,server_cpu,client_ram,server_ram,expected',[
    (0,0,0,0,[]),
    (3,2,0,0,['同机双方 CPU 申请之和超过节点容量']),
    (0,0,3,2,['同机双方内存申请之和超过节点容量']),
    (2,2,2,2,[]),
])
def test_same_node_pair_sums_effective_requests(client_cpu,server_cpu,client_ram,server_ram,expected):
    planner,client,definition=scenario(same_node=True)
    for pool in planner.pools.values():
        pool.cpu_per_job=pool.ram_gb_per_job=0
    planner.workers['s'].cpu_capacity=planner.workers['s'].ram_gb_capacity=4
    client.requested_cpu=client_cpu or None
    client.requested_ram_gb=client_ram or None
    definition['resources']['cpus']=server_cpu or None
    definition['resources']['ram_gb']=server_ram or None
    assert planner.structural_pair_diagnosis(client,definition)==(not expected,expected)
