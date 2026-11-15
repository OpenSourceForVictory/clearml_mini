from sqlalchemy import select
from backend.tests.test_execution import env,node
from backend.tests.test_typed_template_flow import create
from backend.app.core import Job,Pool


def test_suite_defaults_case_parameters_and_resources_stay_separate(env):
    main,c,a=env;t=create(env)
    cases=[{'case_id':'a','task_name':'one','episodes':1,'parameters':{},'service_keys':['policy_a'],'resources':{'cpus':1,'ram_gb':1}},
           {'case_id':'b','task_name':'two','episodes':1,'parameters':{'scene':'other','difficulty':2},'service_keys':['policy_b'],'resources':{'cpus':2,'ram_gb':2}}]
    with main.SessionLocal() as s:p=s.get(Pool,'mock');p.cpu_per_job=2;p.ram_gb_per_job=2;s.commit()
    r=c.post('/api/v1/suites',headers=a['alice'],json={'name':'defaults','version':'1','template_id':t,'client_defaults':{'scene':'default'},'cases':cases})
    assert r.status_code==201,r.text
    suite=r.json()
    body={'title':'run','pool_id':'mock','suite_id':suite['id'],'client':{'template_id':t,'endpoint':'http://192.168.8.1:9000','parameter_overrides':{'difficulty':4}}}
    r=c.post('/api/v1/batches',headers=a['alice'],json=body);assert r.status_code==201,r.text
    with main.SessionLocal() as s:
        jobs=list(s.scalars(select(Job).order_by(Job.case_id)))
        assert [(j.requested_cpu,j.requested_ram_gb) for j in jobs]==[(1,1),(2,2)]
        assert [j.client_spec['parameters'] for j in jobs]==[{'scene':'default','difficulty':4},{'scene':'other','difficulty':4}]
        assert jobs[0].client_spec['parameter_sources']=={'scene':'suite','difficulty':'override'}
        assert [j.client_spec['service_keys'] for j in jobs]==[['policy_a'],['policy_b']]
    r=c.post('/api/v1/batches/preflight',headers=a['alice'],json=body)
    assert r.status_code==200 and [x['case_id'] for x in r.json()['case_checks']]==['a','b']


def test_resource_budget_rejects_oversize_without_partial_jobs(env):
    main,c,a=env;t=create(env)
    body={'title':'bad','pool_id':'mock','client':{'template_id':t,'endpoint':'http://192.168.8.1:9000'},
          'cases':[{'case_id':'a','task_name':'one','episodes':1,'parameters':{'scene':'room'},'resources':{'cpus':10000}}]}
    assert c.post('/api/v1/batches',headers=a['alice'],json=body).status_code==400
    with main.SessionLocal() as s:assert list(s.scalars(select(Job)))==[]
    body['cases'][0]['resources']={'npu_memory_gb':1}
    assert c.post('/api/v1/batches',headers=a['alice'],json=body).status_code==400
