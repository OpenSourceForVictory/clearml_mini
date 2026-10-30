import json
import pytest
from sqlalchemy import select
from backend.tests.test_execution import env
from backend.tests.test_cs_protocol import template, batch_body
from backend.app.core import EvaluationSuite, Job
from backend.app.suite_import import normalize, catalog


def test_import_defaults_ids_errors_and_catalog():
    r=normalize('''schema_version: 1
name: mini
version: "1"
defaults:
  episodes: 2
  seed: 8
  parameters: {mode: joint, limit: 10}
cases:
  - task_name: adjust_bottle
  - task_name: beat_block_hammer
    parameters: {limit: 4}
''')
    assert r['valid'],r
    assert r['total_episodes']==4
    cases=r['document']['cases']
    assert cases[0]['case_id']=='case-001'
    assert cases[1]['parameters']=={'mode':'joint','limit':4}
    assert normalize(json.dumps(r['document']))['content_hash']==r['content_hash']
    assert len(catalog()['cases'])==50
    invalid=normalize('{"cases":[{"task_name":"x","episodes":0}]}')
    assert not invalid['valid'] and invalid['errors'][0]['field']=='cases.0.episodes'
    assert not normalize('[{"case_id":"x","task_name":"a"},{"case_id":"x","task_name":"b"}]')['valid']


@pytest.mark.parametrize('text',[
    'cases: &a [*a]', '!!python/object/apply:os.system ["echo unsafe"]',
    'cases: []\ncases: []', '{"cases":[],"cases":[]}',
    'cases: [{task_name: x, parameters: {date: 2026-01-01}}]',
    'cases: [{task_name: x, parameters: {value: .nan}}]',
    'cases: [{task_name: x, parameters: {!!int 1: value}}]',
    '['*1000+']'*1000, 'x'*1048577, '{"schema_version":2,"cases":[]}'
])
def test_unsafe_or_unsupported_import_is_rejected(text):
    assert not normalize(text)['valid']


def test_lifecycle_snapshot_and_permissions(env):
    main,c,auth=env;t=template(env)
    body={'name':'demo','version':'1','template_id':t,'cases':batch_body(t)['cases'],'status':'draft','tags':['robot']}
    r=c.post('/api/v1/suites',headers=auth['alice'],json=body)
    assert r.status_code==201,r.text
    x=r.json();path='/api/v1/suites/'+x['id']
    request={**batch_body(t),'suite_id':x['id'],'cases':[]}
    assert c.post('/api/v1/batches',headers=auth['alice'],json=request).status_code==409
    assert c.get(path,headers=auth['bob']).status_code==404
    assert c.get(path+'/export',headers=auth['bob']).status_code==404
    assert c.post(path+'/actions',headers=auth['bob'],json={'action':'delete','expected_revision':0}).status_code==404
    edit={**body,'description':'updated','expected_revision':0}
    assert c.put(path,headers=auth['alice'],json=edit).status_code==200
    assert c.put(path,headers=auth['alice'],json=edit).status_code==409
    assert c.post(path+'/actions',headers=auth['alice'],json={'action':'publish','expected_revision':1}).status_code==200
    assert c.put(path,headers=auth['alice'],json={**edit,'expected_revision':2}).status_code==409
    run=c.post('/api/v1/batches',headers=auth['alice'],json=request)
    assert run.status_code==201,run.text
    assert len(c.get(path+'/runs',headers=auth['alice']).json())==1
    with main.SessionLocal() as s:
        before=[(j.id,j.client_spec) for j in s.scalars(select(Job))]
    assert c.post(path+'/actions',headers=auth['alice'],json={'action':'archive','expected_revision':2}).status_code==200
    assert c.get('/api/v1/suites',headers=auth['alice']).json()==[]
    assert c.post('/api/v1/batches',headers=auth['alice'],json=request).status_code==409
    assert c.post(path+'/actions',headers=auth['alice'],json={'action':'delete','expected_revision':3}).status_code==409
    assert c.post(path+'/actions',headers=auth['alice'],json={'action':'restore','expected_revision':3}).status_code==200
    with main.SessionLocal() as s:
        assert [(j.id,j.client_spec) for j in s.scalars(select(Job))]==before
    copied=c.post('/api/v1/suites',headers=auth['alice'],json={**body,'version':'2'}).json()
    assert c.post('/api/v1/suites/'+copied['id']+'/actions',headers=auth['alice'],json={'action':'delete','expected_revision':0}).json()=={'deleted':True}


def test_preview_export_duplicate_scope_and_listing(env):
    main,c,auth=env;t=template(env)
    body={'name':'robot','version':'1','template_id':t,'cases':batch_body(t)['cases'],'tags':['smoke'],'source':'pinned'}
    x=c.post('/api/v1/suites',headers=auth['alice'],json=body).json()
    exported=c.get('/api/v1/suites/'+x['id']+'/export',headers=auth['alice']).json()
    assert 'template_id' not in exported and 'id' not in exported
    for user,n in [('alice',1),('bob',0)]:
        r=c.post('/api/v1/suites/preview',headers=auth[user],json={'content':json.dumps(exported)}).json()
        assert r['valid'] and len(r['duplicates'])==n
        assert r['template_id']==t
    with main.SessionLocal() as s:
        for i in range(22):
            s.add(EvaluationSuite(owner_id='alice',name='other'+str(i),version='1',template_id=t,cases=body['cases'],status='draft'))
        s.commit()
    r=c.get('/api/v1/suites/manage',headers=auth['alice']).json()
    assert r['total']==23 and len(r['items'])==20 and 'cases' not in r['items'][0]
    assert len(c.get('/api/v1/suites/manage?page=2',headers=auth['alice']).json()['items'])==3
    assert c.get('/api/v1/suites/manage?tag=smoke&q=robot&state=published',headers=auth['alice']).json()['total']==1
    assert c.get('/api/v1/suites/manage',headers=auth['bob']).json()['total']==0
