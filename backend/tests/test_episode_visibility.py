import pytest
from sqlalchemy import select
from backend.tests.test_execution import env
from backend.tests.test_cs_protocol import prepare
from backend.app.core import Job, Run
from runner.episode_contract import validate_event

@pytest.mark.parametrize('code',[0,7,None])
def test_failed_protocol_exit_is_visible_without_fabricating_results(env,code):
    main,c,auth=env
    _,token,_,job,task=prepare(env)
    with main.SessionLocal() as s:
        j=s.get(Job,job);j.episodes=101;s.commit()
        rid=s.scalar(select(Run.id).where(Run.job_id==job))
    url=f'/api/v1/worker/executions/{task["execution_id"]}/complete'
    body={'status':'FAILED','error':'missing protocol final result'}
    if code is not None:body['exit_code']=code
    assert c.post(url,headers=token,json=body).status_code==200
    path=f'/api/v1/jobs/{job}/attempts/{rid}/episodes'
    legacy=c.get(path,headers=auth['alice']).json()
    assert legacy['items']==[] and legacy['exit_code']==code
    first=c.get(path+'?include_planned=true',headers=auth['alice']).json()
    assert len(first['items'])==100 and first['run_status']=='FAILED'
    assert first['result_source']=='protocol'
    assert all(r['result'] is None and r['started'] is None for r in first['items'])
    assert first['items'][0]['seed']==7
    last=c.get(path+'?include_planned=true&page=2',headers=auth['alice']).json()
    assert [r['episode_id'] for r in last['items']]==[101]
    assert c.get(path+'?include_planned=true&page=3',headers=auth['alice']).json()['items']==[]
    assert c.get(path,headers=auth['bob']).status_code==404

@pytest.mark.parametrize('code',[True,'0',256,-256,0.5])
def test_invalid_exit_code_rejected(env,code):
    _,c,_=env
    _,token,_,_,task=prepare(env)
    assert c.post(f'/api/v1/worker/executions/{task["execution_id"]}/complete',
                  headers=token,json={'status':'FAILED','exit_code':code}).status_code==400

@pytest.mark.parametrize('code',[0,7,-9,None])
def test_optional_episode_exit_code(code):
    event={'episode_id':1,'seed':7,'status':'ERROR','error':'missing result'}
    if code is not None:event['exit_code']=code
    assert validate_event(event)==event

@pytest.mark.parametrize('code',[True,'0',256,-256])
def test_invalid_episode_exit_code(code):
    with pytest.raises(ValueError):
        validate_event({'episode_id':1,'seed':7,'status':'ERROR','error':'failed','exit_code':code})

def test_start_cannot_claim_an_exit():
    with pytest.raises(ValueError):
        validate_event({'episode_id':1,'seed':7,'status':'STARTED','exit_code':0})

def test_exit_migration_preserves_existing_runs_and_is_reversible():
    import importlib
    from sqlalchemy import create_engine, text, inspect
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    migration=importlib.import_module('backend.alembic.versions.x407a89_execution_exit_code')
    engine=create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE evaluation_runs (id TEXT PRIMARY KEY)'))
        connection.execute(text("INSERT INTO evaluation_runs (id) VALUES ('old')"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
            assert connection.execute(text('SELECT exit_code FROM evaluation_runs')).scalar() is None
            connection.execute(text('UPDATE evaluation_runs SET exit_code=7'))
            assert connection.execute(text('SELECT exit_code FROM evaluation_runs')).scalar()==7
            migration.downgrade()
        assert [c['name'] for c in inspect(connection).get_columns('evaluation_runs')]==['id']
        assert connection.execute(text('SELECT id FROM evaluation_runs')).scalar()=='old'
