import copy
import json
import os
import subprocess
import sys
import pytest
from pathlib import Path

from sqlalchemy import select
from backend.tests.test_execution import env, node
from backend.tests.test_cs_protocol import batch_body, submit_body
from backend.app.core import ClientTemplate, EvaluationBatch, Job, Slot, now
from backend.app.scheduling import tick
from backend.app.clients import TemplateCreate

SCHEMA={'version':1,'fields':{
    'scene':{'type':'string','required':True,'binding':{'target':'argv','name':'--scene'}},
    'difficulty':{'type':'integer','default':1,'min':1,'max':5,'overridable':True,'binding':{'target':'env','name':'DIFFICULTY'}},
}}


def create(env):
    r=env[1].post('/api/v1/admin/client-templates',headers=env[2]['admin'],json={
        'name':'typed','version':'1','image':'approved:cs','command':['python3','business.py'],
        'parameter_schema':SCHEMA})
    assert r.status_code==201,r.text
    assert r.json()['runtime'] is not None
    return r.json()['id']


def test_batch_validation_snapshot_and_clone(env):
    main,c,auth=env;t=create(env);body=batch_body(t)
    body['cases'][0]['parameters']={'scene':'first'}
    body['cases'][1]['parameters']={'scene':17}
    r=c.post('/api/v1/batches',headers=auth['alice'],json=body)
    assert r.status_code==400 and 'case b' in r.text
    with main.SessionLocal() as s:
        assert not list(s.scalars(select(Job))) and not list(s.scalars(select(EvaluationBatch)))
    body['cases'][1]['parameters']={'scene':'second','difficulty':2}
    body['client']['parameter_overrides']={'difficulty':4}
    r=c.post('/api/v1/batches',headers=auth['alice'],json=body);assert r.status_code==201,r.text
    with main.SessionLocal() as s:
        jobs=list(s.scalars(select(Job).order_by(Job.case_id)))
        assert [j.client_spec['parameters'] for j in jobs]==[{'scene':'first','difficulty':4},{'scene':'second','difficulty':4}]
        changed=copy.deepcopy(SCHEMA);changed['fields']['difficulty']['default']=5
        s.get(ClientTemplate,t).parameter_schema=changed;s.commit()
        assert jobs[0].client_spec['template']['parameter_schema']['fields']['difficulty']['default']==1
    clone=c.post(f'/api/v1/admin/client-templates/{t}/clone',headers=auth['admin'],json={'version':'2'})
    assert clone.status_code==201 and clone.json()['parameter_schema']==changed


def test_suite_rejects_invalid_business_values_and_locked_override(env):
    _,c,auth=env;t=create(env);cases=batch_body(t)['cases']
    r=c.post('/api/v1/suites',headers=auth['alice'],json={'name':'invalid','version':'1','template_id':t,'cases':cases})
    assert r.status_code==400 and 'scene' in r.text
    body=submit_body(t);body['client']['parameters']={'scene':'one'};body['client']['parameter_overrides']={'scene':'two'}
    assert c.post('/api/v1/jobs',headers=auth['alice'],json=body).status_code==400


def test_registration_rejects_binding_runtime_collision(env):
    definition={'name':'conflict','version':'1','image':'approved:cs','command':['python3','business.py'],
                'parameter_schema':SCHEMA,'runtime':{'overridable_env':['DIFFICULTY']}}
    r=env[1].post('/api/v1/admin/client-templates',headers=env[2]['admin'],json=definition)
    assert r.status_code==400
    with pytest.raises(ValueError,match='conflicts'): TemplateCreate(**definition)
    with env[0].SessionLocal() as s: assert not list(s.scalars(select(ClientTemplate)))


def test_old_worker_never_reserves_typed_template(env):
    main,c,auth=env;t=create(env);token,slots=node(env)
    body=submit_body(t);body['client']['parameters']={'scene':'one'}
    assert c.post('/api/v1/jobs',headers=auth['alice'],json=body).status_code==201
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0]);slot.protocol_version=7;slot.client_images=['approved:cs'];s.commit()
    assert tick(main.SessionLocal)==0
    response=c.post('/api/v1/worker/register',headers=token,json={'pool_id':'mock','gpu_index':0,'gpu_uuid':'mock:node:0','gpu_model':'virtual','vram_gb':0,'protocol_version':8,'client_images':['approved:cs']})
    assert response.status_code==200,response.text
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0]);slot.storage_report={'revision':0,'status':'applied'};slot.storage_reported_at=now()
        slot.runtime_report={'revision':0,'applied_revision':0,'images':{'approved:cs':{'ready':True}},'assets':{}}
        slot.runtime_reported_at=now();s.commit()
    assert tick(main.SessionLocal)==1
    task=c.post(f'/api/v1/worker/slots/{slots[0]}/claim',headers=token).json()['task']
    assert task['client_spec']['parameters']=={'scene':'one','difficulty':1}
    assert task['client_spec']['template']['parameter_schema']==SCHEMA


def test_real_sdk_command_receives_typed_values_each_episode(tmp_path):
    script=tmp_path/'business.py'
    script.write_text('''import sys, os, json
from pathlib import Path
assert sys.argv[1:] == ['--scene', 'room with spaces; echo untouched']
assert os.environ['DIFFICULTY'] == '3'
cfg=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
assert cfg['parameters']['difficulty']==3
Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({'success':False,'metrics':{'difficulty':3}}))
''')
    work=tmp_path/'work';work.mkdir();output=tmp_path/'output';output.mkdir()
    template=TemplateCreate(name='test',version='1',image='approved:cs',command=[sys.executable,str(script)],integration_mode='sdk-command',parameter_schema=SCHEMA).model_dump()
    config={'template':template,'task_name':'task','episodes':2,'seed':7,'endpoint':'http://example.test',
            'parameters':{'scene':'room with spaces; echo untouched','difficulty':3}}
    inp=tmp_path/'input.json';inp.write_text(json.dumps(config))
    result=subprocess.run([sys.executable,'-m','runner.command_sdk'],env={**os.environ,'MINI_EVAL_INPUT':str(inp),'MINI_EVAL_WORK_DIR':str(work),'MINI_EVAL_OUTPUT_DIR':str(output)},capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
    report=json.loads((output/'report.json').read_text())
    assert report['metrics']['episode_count']==2 and report['metrics']['success_rate']==0


def test_direct_entry_receives_business_arguments_and_environment(tmp_path, monkeypatch):
    from runner import cs_entry
    script=tmp_path/'direct.py';result=tmp_path/'received.json'
    script.write_text("import json,sys,os\nfrom pathlib import Path\nPath(sys.argv[1]).write_text(json.dumps({'args':sys.argv[2:],'difficulty':os.environ['DIFFICULTY']}))\n")
    inp=tmp_path/'input.json'
    config={'contract_version':cs_entry.VERSION,'task_name':'direct','endpoint':'http://example.test','request_timeout_seconds':1,
            'parameters':{'scene':'direct room','difficulty':2},
            'template':{'command':[sys.executable,str(script),str(result)],'parameter_schema':SCHEMA}}
    inp.write_text(json.dumps(config));monkeypatch.setenv('MINI_EVAL_INPUT',str(inp));monkeypatch.setenv('MINI_EVAL_OUTPUT_DIR',str(tmp_path/'out'))
    monkeypatch.setattr(cs_entry,'preflight',lambda *args:None)
    with pytest.raises(SystemExit) as exit:cs_entry.main()
    assert exit.value.code==0
    assert json.loads(result.read_text())=={'args':['--scene','direct room'],'difficulty':'2'}
