from backend.tests.test_execution import env
from backend.tests.test_orchestrations import setup,submit
from backend.app.core import Pool,Slot,Worker,ServiceInstance,Run,Job,now
from backend.app.scheduling import tick
from sqlalchemy import select

def test_same_slot_two_service_claims(env,monkeypatch):
 main,c,a=env;st,ss,ct,cs,body=setup(env,monkeypatch)
 with main.SessionLocal() as s:
  p=s.get(Pool,'mock');p.gpu_model='*';p.allow_shared=True;p.max_tasks_per_gpu=4;p.vram_reserve_gb=0
  slot=s.get(Slot,ss);w=s.get(Worker,slot.worker_id);w.roles=['server','client'];w.cpu_capacity=32;w.ram_gb_capacity=64
  slot.gpu_model='GPU';slot.vram_mb=32*1024;slot.free_vram_mb=32*1024;slot.gpu_utilization=0;slot.telemetry_at=now()
  for identity in cs:s.get(Worker,s.get(Slot,identity).worker_id).enabled=False
  s.commit()
 body.update(gpu_mode='shared',requested_vram_gb=8,max_concurrent=1)
 body['services'][0].update(instance_count=2,resources={'gpu_mode':'shared','requested_vram_gb':8})
 batch=submit(env,body);assert tick(main.SessionLocal)==3
 first=c.post('/api/v1/worker/slots/'+ss+'/claim',headers=st,json={'known_execution_ids':[]}).json()['task']
 assert first['status']=='STARTING'
 assert c.post('/api/v1/worker/heartbeat',headers=st,json={'slot_id':ss,'execution_id':first['execution_id'],'started':True}).status_code==200
 second=c.post('/api/v1/worker/slots/'+ss+'/claim',headers=st,json={'known_execution_ids':[first['execution_id']]}).json()['task']
 assert second and second['status']=='STARTING'
 assert first['execution_id']!=second['execution_id'] and first['job_id']!=second['job_id']
 assert first['client_spec']['service']['port']!=second['client_spec']['service']['port']
 with main.SessionLocal() as s:
  instances=list(s.scalars(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id==batch['id'])))
  assert len(instances)==2 and len({i.id for i in instances})==2
  assert len({i.name for i in instances})==1
  assert {i.id for i in instances}=={first['service_instance_id'],second['service_instance_id']}
  assert {i.current_job_id for i in instances}=={first['job_id'],second['job_id']}
