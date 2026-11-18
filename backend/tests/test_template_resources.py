import copy
import pytest
from sqlalchemy import select
from backend.tests.test_execution import env
from backend.app.core import ClientTemplate,ServerTemplate,Job,Pool
from backend.tests.test_cs_protocol import batch_body,submit_body

CONTRACT={'version':1,'cpu':{'minimum':1,'default':1},'memory':{'minimum':1,'default':1},'accelerator':{'type':'gpu','mode':'exclusive','memory':{'minimum':0,'default':0}},'allow_overrides':['cpus','ram_gb']}

def template(env,kind='client'):
 r=env[1].post('/api/v1/admin/'+kind+'-templates',headers=env[2]['admin'],json={'name':'resources-'+kind,'version':'1','image':'image:1','command':['python','main.py'],'resource_requirements':CONTRACT})
 assert r.status_code==201,r.text
 return r.json()

@pytest.mark.parametrize('kind',['client','server'])
def test_registration_copy_and_invalid_default(env,kind):
 t=template(env,kind);c,a=env[1:]
 r=c.post(f'/api/v1/admin/{kind}-templates/{t["id"]}/clone',headers=a['admin'],json={'version':'2'})
 assert r.status_code==201 and r.json()['resource_requirements']==CONTRACT
 bad=copy.deepcopy(CONTRACT);bad['cpu']['default']=0
 r=c.post('/api/v1/admin/'+kind+'-templates',headers=a['admin'],json={'name':'bad','version':'1','image':'image:1','command':['x'],'resource_requirements':bad})
 assert r.status_code==400

@pytest.mark.parametrize('endpoint',['jobs','batches','batches/preflight'])
@pytest.mark.parametrize('resources',[{}, {'cpus':1}, {'ram_gb':0}, {'cpus':None}, {'accelerator_type':'npu'}, {'gpu_mode':'shared','requested_vram_gb':2}])
def test_entrypoints_resolve_same_contract(env,endpoint,resources):
 main,c,a=env;t=template(env)
 body=submit_body(t['id']) if endpoint=='jobs' else batch_body(t['id'])
 body['resources']=resources
 r=c.post('/api/v1/'+endpoint,headers=a['alice'],json=body)
 valid=resources in ({},{'cpus':1})
 assert r.status_code==(200 if endpoint.endswith('preflight') else 201) if valid else r.status_code==400,r.text
 if valid and endpoint.endswith('preflight'):
  assert r.json()['case_checks'][0]['resources']['cpus']==1
 elif valid:
  with main.SessionLocal() as s:
   jobs=list(s.scalars(select(Job)));assert jobs
   assert all(j.requested_cpu==1 and j.requested_ram_gb==1 for j in jobs)
   assert all(j.client_spec['resources']['accelerator_type']=='gpu' for j in jobs)


@pytest.mark.parametrize('unlimited',[False,True])
def test_service_snapshot_survives_template_change(env,unlimited):
 from backend.tests.test_services import setup
 from backend.app.services import enqueue
 from backend.app.core import ServiceInstance,User
 main,c,a=env;_,slot,body=setup(env);t=template(env,'server');body['template_id']=t['id']
 if unlimited:body['resources']={'memory_unlimited':True,'ram_gb':4}
 r=c.post('/api/v1/admin/services',headers=a['admin'],json=body);assert r.status_code==201,r.text
 with main.SessionLocal() as s:
  instance=s.get(ServiceInstance,r.json()['id']);original=instance.resources.copy()
  if unlimited:
   assert original['ram_gb']==4 and original['memory_unlimited'] is True
   assert s.get(Job,instance.current_job_id).client_spec['resources']['memory_unlimited'] is True
  changed=copy.deepcopy(CONTRACT);changed['cpu']['default']=2
  st=s.get(ServerTemplate,t['id']);st.definition={**st.definition,'resource_requirements':changed};s.commit()
  s.get(Job,instance.current_job_id).status='FAILED';s.commit()
  enqueue(s,instance,s.get(User,'admin'));s.flush()
  assert instance.resources==original and s.get(Job,instance.current_job_id).requested_cpu==1


def test_managed_preflight_and_submission_client_server_resources(env,monkeypatch):
 from backend.tests.test_orchestrations import setup
 main,c,a=env;*_,body=setup(env,monkeypatch)
 with main.SessionLocal() as s:
  client=s.get(ClientTemplate,body['client']['template_id']);client.resource_requirements=CONTRACT
  server=s.get(ServerTemplate,body['services'][0]['template_id']);server.definition={**server.definition,'resource_requirements':CONTRACT};s.commit()
 for path in ['orchestrations/preflight','orchestrations']:
  bad=copy.deepcopy(body);bad['services'][0]['resources']={'accelerator_type':'npu'}
  r=c.post('/api/v1/'+path,headers={**a['alice'],'Idempotency-Key':'bad'},json=bad);assert r.status_code==400,r.text
  r=c.post('/api/v1/'+path,headers={**a['alice'],'Idempotency-Key':'good'},json=body);assert r.status_code in (200,201),r.text
  if path.endswith('preflight'):assert r.json()['service_plan'][0]['resources']['cpus']==1
 with main.SessionLocal() as s:
  assert all(j.requested_cpu==1 for j in s.scalars(select(Job)))
