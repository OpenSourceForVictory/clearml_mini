from backend.tests.test_execution import env,node
from backend.app.host_environments import blockers
from backend.app.core import Worker,Slot,now
from types import SimpleNamespace


def test_host_environment_version_and_readiness(env):
    main,c,a=env;token,slots=node(env)
    n=c.get('/api/v1/admin/nodes',headers=a['admin']).json()[0]
    path='/api/v1/admin/nodes/'+n['id']+'/host-environments'
    config={'simulation':{'python':'/usr/bin/python3','code_root':'/srv/client'}}
    assert c.put(path,headers=a['alice'],json={'expected_revision':0,'environments':config}).status_code==403
    assert c.put(path,headers=a['admin'],json={'expected_revision':0,'environments':config}).status_code==200
    assert c.put(path,headers=a['admin'],json={'expected_revision':0,'environments':config}).status_code==409
    with main.SessionLocal() as s:
        w=s.get(Worker,n['id']);slot=s.get(Slot,slots[0]);spec={'template':{'execution_mode':'host','host_runtime':{'environment_name':'simulation','assets':[]}}}
        assert blockers(w,slot,spec,now())==['宿主机执行需要 Worker v7']
        slot.protocol_version=7
        assert blockers(w,slot,spec,now())
        slot.host_reported_at=now();slot.host_report={'revision':1,'applied_revision':1,'environments':{'simulation':{'ready':False}}}
        assert blockers(w,slot,spec,now())
        slot.host_report['environments']['simulation']['ready']=True
        assert blockers(w,slot,spec,now())==[]
