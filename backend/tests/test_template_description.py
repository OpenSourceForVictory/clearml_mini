import importlib
import pytest
from sqlalchemy import create_engine, text
from alembic.migration import MigrationContext
from alembic.operations import Operations
from backend.tests.test_execution import env
from backend.tests.test_generic_templates import definition
from backend.tests.test_template_versions import fields
from backend.app.core import ServerTemplate
from backend.app.templates import server_view


@pytest.mark.parametrize('kind', ['client', 'server'])
@pytest.mark.parametrize('mode', ['host', 'docker'])
def test_description_lifecycle_and_validation(env, kind, mode):
    factory, c, a = env
    path = '/api/v1/admin/'+kind+'-templates'
    description = '用途：评测\n依赖 CANN <script>alert(1)</script> ${task_name}'
    body = {**definition(mode), 'description': description}
    created = c.post(path, headers=a['admin'], json=body)
    assert created.status_code == 201, created.text
    source = created.json()
    assert source['description'] == description
    copied = c.post(path+'/'+source['id']+'/clone', headers=a['admin'], json={'version':'2'})
    assert copied.status_code == 201 and copied.json()['description'] == description
    r = c.post('/api/v1/runtime-templates/'+kind+'/'+source['id']+'/clone', headers=a['admin'], json={'version':'3'})
    draft = r.json(); assert r.status_code == 201
    values = fields(draft, kind=='server'); values['description'] = '新说明'
    target = '/api/v1/runtime-templates/'+kind+'/'+draft['id']
    assert c.put(target, headers=a['bob'], json={'expected_revision':0,'definition':values}).status_code == 404
    changed = c.put(target, headers=a['admin'], json={'expected_revision':0,'definition':values})
    assert changed.status_code == 200 and changed.json()['description'] == '新说明'
    assert changed.json()['command'] == source['command']
    assert c.put(target, headers=a['admin'], json={'expected_revision':0,'definition':values}).status_code == 409
    rows=c.get('/api/v1/runtime-templates/'+kind,headers=a['admin']).json()
    assert next(x for x in rows if x['id']==source['id'])['description']==description
    for i, bad in enumerate(['x'*2001, 'bad\x00text', None, 7]):
        assert c.post(path, headers=a['admin'], json={**body,'version':'bad'+str(i),'description':bad}).status_code == 400
    for i, valid in enumerate(['', '字'*2000]):
        r=c.post(path,headers=a['admin'],json={**body,'version':'valid'+str(i),'description':valid})
        assert r.status_code==201 and r.json()['description']==valid
    r=c.post(path,headers=a['admin'],json={**definition(mode),'version':'legacy'})
    assert r.status_code==201 and r.json()['description']==''
    if kind=='server':
        with factory.SessionLocal() as s:
            t=s.get(ServerTemplate,r.json()['id']);data=dict(t.definition);data.pop('description');t.definition=data;s.commit()
            assert server_view(t)['description']==''


def test_description_migration_preserves_existing_rows():
    module=importlib.import_module('backend.alembic.versions.ac963d4_template_description')
    engine=create_engine('sqlite://')
    with engine.begin() as c:
        c.execute(text('CREATE TABLE client_templates (id TEXT PRIMARY KEY)'))
        c.execute(text("INSERT INTO client_templates VALUES ('old')"))
        with Operations.context(MigrationContext.configure(c)):
            module.upgrade()
            assert c.execute(text('SELECT description FROM client_templates')).scalar_one()==''
            module.downgrade()
            assert c.execute(text('SELECT id FROM client_templates')).scalar_one()=='old'
    engine.dispose()


def test_client_snapshot_description_does_not_modify_command_or_original(env):
    from backend.app.clients import ClientRequest, snapshot
    from backend.app.core import ClientTemplate
    main,c,a=env
    body={**definition('host'),'description':'模板说明 ${task_name} <b>hello</b>'}
    t=c.post('/api/v1/admin/client-templates',headers=a['admin'],json=body).json()
    with main.SessionLocal() as s:
        original=snapshot(s,ClientRequest(template_id=t['id'],endpoint='http://inference:8000',task_name='test',seed=1))
        row=s.get(ClientTemplate,t['id']);row.description='changed';s.commit()
        assert original['template']['description']==body['description']
        assert original['template']['command']==body['command']
        assert original['parameters']=={}
