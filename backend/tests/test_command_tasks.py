import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import select
from backend.tests.test_execution import env,node,claim
from backend.app.core import Slot,Job,now
from backend.app.scheduling import tick


def definition():
    return {'name':'command','version':'1','image':'approved:cs','command':['python3','business.py'],
            'task_type':'command','platform_bindings':[{'source':'task.task_name','target':'argv','name':'--task'}]}


def create(env):
    _,c,a=env
    r=c.post('/api/v1/admin/client-templates',headers=a['admin'],json=definition())
    assert r.status_code==201,r.text
    return r.json()['id']


def submit(env,t):
    return env[1].post('/api/v1/jobs',headers=env[2]['alice'],json={
        'title':'plain command','benchmark':'cs-client','model_ref':'command','episodes':1,'pool_id':'mock',
        'client':{'template_id':t,'service_mode':'none','task_name':'batch-work'}})


def test_command_admission_protocol_and_completion(env):
    main,c,a=env;t=create(env);token,slots=node(env)
    response=submit(env,t);assert response.status_code==201,response.text
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0]);slot.protocol_version=9;slot.client_images=['approved:cs']
        slot.storage_report={'revision':0,'status':'applied'};slot.storage_reported_at=now()
        slot.runtime_report={'revision':0,'applied_revision':0,'images':{'approved:cs':{'ready':True}},'assets':{}}
        slot.runtime_reported_at=now();s.commit()
    assert tick(main.SessionLocal)==0
    with main.SessionLocal() as s:s.get(Slot,slots[0]).protocol_version=10;s.commit()
    task=claim(env,token,slots[0]);assert task and task['client_spec']['service_mode']=='none'
    assert 'endpoint' not in task['client_spec']
    r=c.post('/api/v1/worker/executions/'+task['execution_id']+'/complete',headers=token,json={'status':'SUCCEEDED'})
    assert r.status_code==200,r.text
    detail=c.get('/api/v1/jobs/'+response.json()['id'],headers=a['alice']).json()
    assert detail['status']=='SUCCEEDED' and detail['attempts'][0]['metrics'] is None


def test_evaluation_cannot_omit_endpoint_and_command_cannot_claim_episodes(env):
    _,c,a=env
    r=c.post('/api/v1/admin/client-templates',headers=a['admin'],json={**definition(),'task_type':'evaluation'})
    assert r.status_code==201,r.text
    assert submit(env,r.json()['id']).status_code==400
    for changes in ({'episode_reporting':True},{'integration_mode':'sdk-command'}):
        r=c.post('/api/v1/admin/client-templates',headers=a['admin'],json={**definition(),**changes})
        assert r.status_code==400,r.text


@pytest.mark.parametrize('code',[0,7])
def test_real_command_entry_receives_normalized_input_and_exit_code(tmp_path,code):
    script=tmp_path/'business.py'
    script.write_text('import os,json,sys\nc=json.load(open(os.environ["MINI_EVAL_INPUT"]))\n'
                      'assert c["platform"]["service"] is None\n'
                      'assert c["business"]=={"scale":3}\n'
                      'assert sys.argv[1:]==["--task","plain task"]\n'
                      'raise SystemExit('+str(code)+')\n')
    c={'contract_version':1,'service_mode':'none','task_name':'plain task','episodes':1,'seed':0,
       'parameters':{'scale':3},'template':{'task_type':'command','execution_mode':'host','command':[sys.executable,str(script)],
       'platform_bindings':definition()['platform_bindings']},'_resolved_environment':{'MINI_EVAL_WORK_DIR':str(tmp_path)}}
    inp=tmp_path/'input.json';inp.write_text(json.dumps(c))
    result=subprocess.run([sys.executable,'-m','runner.cs_entry'],env={**os.environ,'MINI_EVAL_INPUT':str(inp),'MINI_EVAL_OUTPUT_DIR':str(tmp_path/'output')},capture_output=True,text=True,timeout=5)
    assert result.returncode==code,result.stderr
    assert 'connecting' not in result.stdout


def test_task_result_sdk_call_writes_execution_scoped_file(tmp_path):
    script=tmp_path/'business.py'
    script.write_text('from runner.sdk import report_task_result\n'
                      'report_task_result(False, {"quality": 0.25}, "not accepted")\n')
    work=tmp_path/'work';work.mkdir()
    c={'contract_version':1,'service_mode':'none','task_name':'plain task','episodes':1,'seed':0,
       'parameters':{},'template':{'task_type':'command','integration_mode':'sdk-task-result',
       'execution_mode':'host','command':[sys.executable,str(script)]},
       '_resolved_environment':{'MINI_EVAL_WORK_DIR':str(work)}}
    inp=tmp_path/'input.json';inp.write_text(json.dumps(c))
    result=subprocess.run([sys.executable,'-m','runner.cs_entry'],env={**os.environ,
        'MINI_EVAL_INPUT':str(inp),'MINI_EVAL_OUTPUT_DIR':str(tmp_path/'output'),
        'PYTHONPATH':str(Path(__file__).resolve().parents[2])},capture_output=True,text=True,timeout=5)
    assert result.returncode==0,result.stderr
    assert json.loads((work/'task-result.json').read_text())=={
        'success':False,'metrics':{'quality':.25},'summary':'not accepted'}


def test_command_supervisor_timeout(tmp_path):
    result=subprocess.run([sys.executable,'-m','runner.supervise','--timeout','1',sys.executable,'-c','import time; time.sleep(30)'],capture_output=True,text=True,timeout=5)
    assert result.returncode==124 and 'deadline exceeded' in result.stderr
