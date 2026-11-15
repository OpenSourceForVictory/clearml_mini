import copy
from backend.tests.test_execution import env
from backend.tests.test_typed_template_flow import SCHEMA,create
from backend.app.clients import TemplateCreate
from backend.app.templates import ServerCreate


def fields(value,server=False):
    return {k:v for k,v in value.items() if k in (ServerCreate if server else TemplateCreate).model_fields}


def test_private_client_copy_edit_publish_and_conflict(env):
    _,c,a=env;t=create(env)
    r=c.post('/api/v1/runtime-templates/client/'+t+'/clone',headers=a['alice'],json={'name':'my-client','version':'1'})
    assert r.status_code==201,r.text
    draft=r.json();assert draft['status']=='draft' and draft['owner_id']=='alice'
    assert draft['parameter_schema']==SCHEMA
    assert draft['id'] not in [x['id'] for x in c.get('/api/v1/client-templates',headers=a['alice']).json()]
    values=fields(draft);values['parameter_schema']=copy.deepcopy(SCHEMA);values['parameter_schema']['fields']['difficulty']['default']=4
    path='/api/v1/runtime-templates/client/'+draft['id']
    assert c.put(path,headers=a['bob'],json={'expected_revision':0,'definition':values}).status_code==404
    r=c.put(path,headers=a['alice'],json={'expected_revision':0,'definition':values});assert r.status_code==200,r.text
    assert c.put(path,headers=a['alice'],json={'expected_revision':0,'definition':values}).status_code==409
    assert c.post(path+'/actions',headers=a['alice'],json={'expected_revision':1,'action':'publish'}).status_code==200
    assert c.put(path,headers=a['alice'],json={'expected_revision':2,'definition':values}).status_code==409
    items=c.get('/api/v1/client-templates',headers=a['alice']).json()
    assert next(x for x in items if x['id']==t)['parameter_schema']==SCHEMA
    assert next(x for x in items if x['id']==draft['id'])['parameter_schema']['fields']['difficulty']['default']==4


def test_server_access_copy_and_definition_isolation(env):
    _,c,a=env
    r=c.post('/api/v1/admin/server-templates',headers=a['admin'],json={'name':'server','version':'1','image':'approved:cs','command':['python','server.py'],'parameter_schema':SCHEMA})
    assert r.status_code==201,r.text
    original=r.json();t=original['id']
    assert c.get('/api/v1/runtime-templates/server',headers=a['alice']).json()==[]
    path='/api/v1/runtime-templates/server/'+t+'/clone'
    assert c.post(path,headers=a['alice'],json={'name':'copy','version':'1'}).status_code==404
    assert c.patch('/api/v1/admin/server-templates/'+t+'/access',headers=a['alice'],json={'expected_revision':0,'available_to_members':True}).status_code==403
    assert c.patch('/api/v1/admin/server-templates/'+t+'/access',headers=a['admin'],json={'expected_revision':0,'available_to_members':True}).status_code==200
    r=c.post(path,headers=a['alice'],json={'name':'copy','version':'1'});assert r.status_code==201,r.text
    draft=r.json();assert draft['owner_id']=='alice' and not draft['available_to_members']
    assert draft['parameter_schema']==SCHEMA
    assert draft['id'] not in [x['id'] for x in c.get('/api/v1/runtime-templates/server',headers=a['bob']).json()]
    values=fields(draft,True);values['parameter_schema']['fields']['scene']['default']='room'
    r=c.put('/api/v1/runtime-templates/server/'+draft['id'],headers=a['alice'],json={'expected_revision':0,'definition':values})
    assert r.status_code==200,r.text
    assert c.patch('/api/v1/admin/server-templates/'+draft['id']+'/access',headers=a['admin'],json={'expected_revision':1,'available_to_members':True}).status_code==409


def test_suite_copy_and_parameter_edit(env):
    _,c,a=env;t=create(env)
    body={'name':'suite','version':'1','template_id':t,'cases':[{'case_id':'one','task_name':'demo','episodes':2,'parameters':{'scene':'room'}}]}
    r=c.post('/api/v1/suites',headers=a['alice'],json=body);assert r.status_code==201,r.text
    source=r.json();path='/api/v1/suites/'+source['id']+'/clone'
    assert c.post(path,headers=a['bob'],json={'name':'copy','version':'1'}).status_code==404
    r=c.post(path,headers=a['alice'],json={'name':'copy','version':'1'});assert r.status_code==201,r.text
    draft=r.json();assert draft['cases']==source['cases'] and draft['status']=='draft'
    updated={**body,'name':'copy','cases':copy.deepcopy(body['cases']),'expected_revision':0,'status':'draft'}
    updated['cases'][0]['parameters']['scene']='new'
    r=c.put('/api/v1/suites/'+draft['id'],headers=a['alice'],json=updated);assert r.status_code==200,r.text
    assert c.get('/api/v1/suites/'+source['id'],headers=a['alice']).json()['cases'][0]['parameters']['scene']=='room'


def test_host_copy_does_not_authorize_runtime_changes(env):
    _,c,a=env
    definition={'name':'host-client','version':'1','execution_mode':'host','command':['python','business.py'],
                'host_runtime':{'environment_name':'approved'},'parameter_schema':SCHEMA}
    r=c.post('/api/v1/admin/client-templates',headers=a['admin'],json=definition);assert r.status_code==201,r.text
    r=c.post('/api/v1/runtime-templates/client/'+r.json()['id']+'/clone',headers=a['alice'],json={'name':'host-copy','version':'1'})
    assert r.status_code==201,r.text
    draft=r.json();values=fields(draft);values['command']=['python','different.py']
    assert c.put('/api/v1/runtime-templates/client/'+draft['id'],headers=a['alice'],json={'expected_revision':0,'definition':values}).status_code==403
    values=fields(draft);values['parameter_schema']['fields']['difficulty']['default']=3
    assert c.put('/api/v1/runtime-templates/client/'+draft['id'],headers=a['alice'],json={'expected_revision':0,'definition':values}).status_code==200
