from backend.tests.test_execution import env,node
from backend.app.core import Worker,Slot
from backend.app.core import now
from datetime import timedelta
from sqlalchemy import select


def test_resource_role_filters_admin_details_and_user_counts(env):
    main,c,a=env
    for name in ('client','server','both'):node(env,name=name)
    with main.SessionLocal() as s:
        for w in s.scalars(select(Worker)):
            w.roles=['client','server'] if w.node_name=='both' else [w.node_name]
        s.commit()
    for role,names in (('',{'client','server','both'}),('client',{'client','both'}),('server',{'server','both'})):
        path='/api/v1/workers'+('?role='+role if role else '')
        values=c.get(path,headers=a['admin']).json()
        assert {v['node_name'] for v in values}==names
        assert next(v for v in values if v['node_name']=='both')['roles']==['client','server']
        summary=c.get(path,headers=a['alice']).json()
        assert summary['total']==len(names) and set(summary)=={'total','online'}
    assert c.get('/api/v1/workers?role=unknown',headers=a['admin']).status_code==400


def test_legacy_unassociated_slot_defaults_to_client(env):
    main,c,a=env
    with main.SessionLocal() as s:
        s.add(Slot(node_name='legacy',gpu_index=0,queue_name='legacy'));s.commit()
    assert c.get('/api/v1/workers?role=server',headers=a['alice']).json()['total']==0
    assert c.get('/api/v1/workers?role=client',headers=a['alice']).json()['total']==1


def test_resource_page_telemetry_fresh_matches_scheduler(env):
    main,c,a=env
    _,slots=node(env,name='freshness')
    slot_id=slots[0]
    for sampled,free,utilization,expected in (
        (now()-timedelta(seconds=2),1024,10,True),
        (now()-timedelta(seconds=16),1024,10,False),
        (now()+timedelta(minutes=2),1024,10,False),
        (now(),None,None,False),
    ):
        with main.SessionLocal() as s:
            slot=s.get(Slot,slot_id)
            slot.telemetry_at=sampled;slot.free_vram_mb=free;slot.gpu_utilization=utilization
            s.commit()
        result=c.get('/api/v1/workers',headers=a['admin'])
        assert result.status_code==200,result.text
        assert next(x for x in result.json() if x['id']==slot_id)['telemetry_fresh'] is expected
