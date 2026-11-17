import json
from sqlalchemy import select
from backend.tests.test_execution import env
from backend.tests.test_cs_protocol import template,batch_body
from backend.app.core import Job,EvaluationSuite,SuiteClientTemplate,ClientTemplate


def fixture(env):
    main,c,a=env;t1=template(env)
    t2=c.post('/api/v1/admin/client-templates/'+t1+'/clone',headers=a['admin'],json={'version':'2'}).json()['id']
    body={'name':'reusable','version':'1','template_id':t1,'cases':batch_body(t1)['cases']}
    suite=c.post('/api/v1/suites',headers=a['alice'],json=body)
    assert suite.status_code==201,suite.text
    return t1,t2,suite.json(),body


def test_published_relation_reuse_and_historical_snapshots(env):
    main,c,a=env;t1,t2,suite,body=fixture(env);path='/api/v1/suites/'+suite['id']
    original=suite['cases']
    r=c.put(path+'/client-templates',headers=a['alice'],json={'expected_revision':0,'template_ids':[t1,t2]})
    assert r.status_code==200,r.text
    assert set(r.json()['template_ids'])=={t1,t2} and r.json()['cases']==original
    assert r.json()['status']=='published'
    for tid in (t1,t2):
        request={**batch_body(tid),'suite_id':suite['id'],'cases':[]}
        run=c.post('/api/v1/batches',headers=a['alice'],json=request)
        assert run.status_code==201,run.text
    with main.SessionLocal() as s:
        before=[(j.id,j.client_spec) for j in s.scalars(select(Job).order_by(Job.id))]
    removed=c.put(path+'/client-templates',headers=a['alice'],json={'expected_revision':1,'template_ids':[t2]})
    assert removed.status_code==200 and removed.json()['template_id']==t2
    assert c.post('/api/v1/batches',headers=a['alice'],json={**batch_body(t1),'suite_id':suite['id'],'cases':[]}).status_code==400
    with main.SessionLocal() as s:
        assert before==[(j.id,j.client_spec) for j in s.scalars(select(Job).order_by(Job.id))]
        assert [x.template_id for x in s.scalars(select(SuiteClientTemplate).where(SuiteClientTemplate.suite_id==suite['id']))]==[t2]
    # The same template may be related to multiple suites.
    other=c.post('/api/v1/suites',headers=a['alice'],json={**body,'name':'another','template_ids':[t1,t2]})
    assert other.status_code==201,other.text
    assert c.get('/api/v1/suites/manage?template_id='+t2,headers=a['alice']).json()['total']==2


def test_relations_authority_revision_and_atomic_validation(env):
    main,c,a=env;t1,t2,suite,_=fixture(env);path='/api/v1/suites/'+suite['id']+'/client-templates'
    change={'expected_revision':0,'template_ids':[t1,t2]}
    assert c.put(path,headers=a['bob'],json=change).status_code==404
    assert c.put(path,headers=a['alice'],json={**change,'expected_revision':3}).status_code==409
    for ids in ([],[t1,t1],['missing']):
        assert c.put(path,headers=a['alice'],json={**change,'template_ids':ids}).status_code in (400,422)
    with main.SessionLocal() as s:
        t=s.get(ClientTemplate,t2);t.parameter_schema={'version':1,'fields':{'required_value':{'type':'string','required':True}}};s.commit()
    bad=c.put(path,headers=a['alice'],json=change)
    assert bad.status_code==400 and 'case' in bad.json()['detail']
    current=c.get('/api/v1/suites/'+suite['id'],headers=a['alice']).json()
    assert current['revision']==0 and current['template_ids']==[t1]
    with main.SessionLocal() as s:
        t=s.get(ClientTemplate,t2);t.parameter_schema=None;t.owner_id='bob';s.commit()
    assert c.put(path,headers=a['alice'],json=change).status_code==400
    with main.SessionLocal() as s:
        t=s.get(ClientTemplate,t2);t.owner_id=None;t.enabled=False;s.commit()
    assert c.put(path,headers=a['alice'],json=change).status_code==400


def test_relation_copy_export_preview_and_legacy(env):
    main,c,a=env;t1,t2,suite,_=fixture(env);path='/api/v1/suites/'+suite['id']
    assert suite['template_ids']==[t1]
    assert c.put(path+'/client-templates',headers=a['alice'],json={'expected_revision':0,'template_ids':[t1,t2]}).status_code==200
    clone=c.post(path+'/clone',headers=a['alice'],json={'name':'reusable','version':'2'})
    assert clone.status_code==201,clone.text
    assert set(clone.json()['template_ids'])=={t1,t2}
    exported=c.get(path+'/export',headers=a['alice']).json();assert len(exported['clients'])==2
    preview=c.post('/api/v1/suites/preview',headers=a['alice'],json={'content':json.dumps(exported)}).json()
    assert set(preview['template_ids'])=={t1,t2} and not preview['unmatched_clients']
    exported['clients'].append({'name':'not-on-this-platform','version':'1'})
    preview=c.post('/api/v1/suites/preview',headers=a['alice'],json={'content':json.dumps(exported)}).json()
    assert len(preview['unmatched_clients'])==1
    # Relation deletion cascades when an unreferenced draft is deleted.
    assert c.post('/api/v1/suites/'+clone.json()['id']+'/actions',headers=a['alice'],json={'action':'delete','expected_revision':0}).status_code==200
    with main.SessionLocal() as s:
        assert not list(s.scalars(select(SuiteClientTemplate).where(SuiteClientTemplate.suite_id==clone.json()['id'])))


def test_archived_and_unpublished_templates_are_not_available(env):
    main,c,a=env;t1,t2,suite,_=fixture(env)
    with main.SessionLocal() as s:
        t=s.get(ClientTemplate,t2);t.status='draft';s.commit()
        hint={'name':t.name,'version':t.version}
    preview=c.post('/api/v1/suites/preview',headers=a['alice'],json={'content':json.dumps({
        'client':hint,'clients':[hint],'cases':suite['cases']})}).json()
    assert preview['valid'] and preview['template_id'] is None
    assert preview['template_ids']==[] and preview['unmatched_clients']==[hint]
    path='/api/v1/suites/'+suite['id']
    assert c.put(path+'/client-templates',headers=a['alice'],json={'expected_revision':0,'template_ids':[t1,t2]}).status_code==400
    assert c.post(path+'/actions',headers=a['alice'],json={'expected_revision':0,'action':'archive'}).status_code==200
    assert c.put(path+'/client-templates',headers=a['alice'],json={'expected_revision':1,'template_ids':[t1]}).status_code==409


def test_relation_migration_backfills_and_refuses_lossy_downgrade(tmp_path):
    import importlib.util
    from pathlib import Path
    from sqlalchemy import create_engine,text,inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    import pytest
    path=Path(__file__).parents[1]/'alembic/versions/v285e67_suite_clients.py'
    spec=importlib.util.spec_from_file_location('suite_relation_migration',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    engine=create_engine('sqlite:///'+str(tmp_path/'migration.sqlite'))
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE client_templates (id VARCHAR(36) PRIMARY KEY)'))
        conn.execute(text('CREATE TABLE evaluation_suites (id VARCHAR(36) PRIMARY KEY, template_id VARCHAR(36))'))
        conn.execute(text("INSERT INTO client_templates VALUES ('t1'),('t2')"))
        conn.execute(text("INSERT INTO evaluation_suites VALUES ('s1','t1')"))
        with Operations.context(MigrationContext.configure(conn)):
            module.upgrade()
            assert conn.execute(text('SELECT suite_id,template_id FROM suite_client_templates')).all()==[('s1','t1')]
            conn.execute(text("INSERT INTO suite_client_templates VALUES ('s1','t2')"))
            with pytest.raises(RuntimeError,match='multiple'):
                module.downgrade()
            assert conn.execute(text('SELECT COUNT(*) FROM suite_client_templates')).scalar()==2
            conn.execute(text("DELETE FROM suite_client_templates WHERE template_id='t2'"))
            module.downgrade()
            assert 'suite_client_templates' not in inspect(conn).get_table_names()
            module.upgrade()
            assert conn.execute(text('SELECT template_id FROM suite_client_templates')).scalar()=='t1'
    engine.dispose()


def test_orchestration_accepts_second_allowed_template_and_rechecks_selection(env,monkeypatch):
    from backend.tests.test_orchestrations import setup
    main,c,a=env;*_,body=setup(env,monkeypatch)
    t1=body['client']['template_id']
    t2=c.post('/api/v1/admin/client-templates/'+t1+'/clone',headers=a['admin'],json={'version':'2'}).json()['id']
    suite={'name':'multiple clients','version':'1','template_ids':[t1,t2],'cases':body['cases']}
    request={**body,'cases':[],'new_suite':suite,'client':{**body['client'],'template_id':t2}}
    preview=c.post('/api/v1/orchestrations/preflight',headers=a['alice'],json=request)
    assert preview.status_code==200,preview.text
    run=c.post('/api/v1/orchestrations',headers={**a['alice'],'Idempotency-Key':'allowed-second'},json=request)
    assert run.status_code==201,run.text
    with main.SessionLocal() as s:
        saved=s.scalar(select(EvaluationSuite).where(EvaluationSuite.name=='multiple clients'))
        identity=saved.id
        assert set(saved.template_ids)=={t1,t2}
    changed=c.put('/api/v1/suites/'+identity+'/client-templates',headers=a['alice'],json={'expected_revision':0,'template_ids':[t1]})
    assert changed.status_code==200,changed.text
    request.pop('new_suite');request['suite_id']=identity
    rejected=c.post('/api/v1/orchestrations',headers={**a['alice'],'Idempotency-Key':'removed-second'},json=request)
    assert rejected.status_code==400,rejected.text
