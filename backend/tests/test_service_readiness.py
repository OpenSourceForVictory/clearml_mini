from datetime import timedelta
from backend.tests.test_execution import env,claim
from backend.tests.test_services import setup
from backend.app.core import Slot,Worker,ServiceInstance,now
from backend.app.service_readiness import fresh


def deploy(env,**options):
    main,c,a=env;token,slot,body=setup(env)
    with main.SessionLocal() as s:
        device=s.get(Slot,slot);device.protocol_version=12;device.software={'capabilities':['service_readiness_v1'],'probe_references':[]}
        worker=s.get(Worker,device.worker_id);worker.service_network={'address':body['address'],'port_start':19000,'port_end':19005}
        s.commit()
    t=c.post('/api/v1/admin/server-templates',headers=a['admin'],json={'name':'ready','version':'1','image':'image:1','command':['python','s.py'],
        'readiness':{'kind':'websocket','port':8001,'success_threshold':2,**options}})
    assert t.status_code==201,t.text
    body['template_id']=t.json()['id']
    response=c.post('/api/v1/admin/services',headers=a['admin'],json=body)
    assert response.status_code==201,response.text
    service=response.json();task=claim(env,token,slot);assert task
    return token,slot,service,task,body


def test_generation_thresholds_and_stale_reports(env):
    main,c,a=env;token,slot,service,task,_=deploy(env)
    path='/api/v1/worker/executions/'+task['execution_id']
    r=c.post(path+'/service-probe-session',headers=token);assert r.status_code==200,r.text
    epoch=r.json()['epoch']
    def post(n,ready=True,**kw):
        return c.post(path+'/service-health',headers=token,json={'ready':ready,'epoch':epoch,'sequence':n,**kw})
    assert not post(1).json()['ready']
    assert post(2).json()['ready']
    assert not post(2,False).json()['accepted']
    assert post(3,False).json()['ready']
    assert post(4,False).json()['ready']
    assert not post(5,False).json()['ready']
    assert not post(6,sample_age_ms=21000).json()['accepted']
    assert not post(6).json()['ready']
    assert post(7).json()['ready']
    with main.SessionLocal() as s:
        item=s.get(ServiceInstance,service['id']);item.health_at=now()-timedelta(seconds=21);s.commit()
        assert not fresh(item)
    assert not post(8).json()['ready']
    epoch2=c.post(path+'/service-probe-session',headers=token).json()['epoch'];assert epoch2!=epoch
    assert post(9).status_code==409
    assert c.post(path+'/service-health',headers=a['alice'],json={'ready':True}).status_code==401


def test_independent_port_is_reserved_until_cleanup(env):
    main,c,a=env;token,slot,service,task,body=deploy(env)
    assert service['health_port'] in range(19000,19006)
    assert task['client_spec']['service']['health_port']==service['health_port']
    # A legacy business port cannot steal an independently reserved health port.
    old=c.get('/api/v1/admin/server-templates',headers=a['admin']).json()
    template=next(t for t in old if t['name']=='server')
    request={**body,'template_id':template['id'],'port':service['health_port']}
    assert c.post('/api/v1/admin/services',headers=a['admin'],json=request).status_code==409
    assert c.post('/api/v1/worker/executions/'+task['execution_id']+'/complete',headers=token,json={'status':'FAILED','error':'test'}).status_code==200
    assert c.post('/api/v1/admin/services',headers=a['admin'],json=request).status_code==201


def test_capability_and_credential_admission():
    from types import SimpleNamespace as N
    from backend.app.image_runtime import blockers
    spec={'template':{'readiness':{'kind':'http','headers_from_env':{'Authorization':'HEALTH_TOKEN'}}}}
    assert '升级' in blockers(None,N(protocol_version=11,software={}),spec,now())[0]
    assert '引用' in blockers(None,N(protocol_version=12,software={'capabilities':['service_readiness_v1']}),spec,now())[0]
