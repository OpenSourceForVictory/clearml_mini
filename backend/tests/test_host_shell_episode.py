import json
import os
import pwd
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from worker.host_identity import login_environment
from backend.tests.test_execution import env
from backend.tests.test_cs_protocol import prepare
from backend.app.core import Job


def test_login_environment_reads_only_exported_values(monkeypatch):
    account=pwd.getpwuid(os.geteuid())
    marker=b'profile output\n\0MINI_EVAL_LOGIN_ENV\0'
    monkeypatch.setattr('worker.host_identity.subprocess.run',lambda *a,**k:SimpleNamespace(
        returncode=0,stdout=marker+b'PATH=/business/bin\0MY_FLAG=ready\0',stderr=b''))
    assert login_environment(account)['MY_FLAG']=='ready'


@pytest.mark.parametrize('output',[
    b'no marker',b'\0MINI_EVAL_LOGIN_ENV\0INVALID\0',
    b'\0MINI_EVAL_LOGIN_ENV\0BAD-NAME=value\0',
    b'\0MINI_EVAL_LOGIN_ENV\0PATH=x',
])
def test_login_environment_rejects_invalid_output(monkeypatch,output):
    account=pwd.getpwuid(os.geteuid())
    monkeypatch.setattr('worker.host_identity.subprocess.run',lambda *a,**k:SimpleNamespace(returncode=0,stdout=output,stderr=b''))
    with pytest.raises(RuntimeError,match='environment'):login_environment(account)


def test_login_environment_timeout_has_no_values(monkeypatch):
    account=pwd.getpwuid(os.geteuid())
    def timeout(*a,**k):raise subprocess.TimeoutExpired('login shell',10)
    monkeypatch.setattr('worker.host_identity.subprocess.run',timeout)
    with pytest.raises(RuntimeError,match='could not be loaded') as error:login_environment(account)
    assert 'SECRET=' not in str(error.value)


def client(tmp_path,script,*,episodes=3,ordinary=False,mode='host',launch='per_episode',env=None,template_extra=None):
    work=tmp_path/'work';work.mkdir();output=tmp_path/'output';output.mkdir()
    template={'execution_mode':mode,'integration_mode':'direct','task_type':'command' if ordinary else 'evaluation',
              'host_runtime':{'command_mode':'exec','episode_launch':launch},'command':[sys.executable,str(script)]}
    template.update(template_extra or {})
    config={'contract_version':1,'service_mode':'none','endpoint':'','task_name':'case','episodes':episodes,'seed':7,
            'parameters':{'scene':'test'},'template':template,'_resolved_command':template['command'],
            '_resolved_environment':{'MINI_EVAL_WORK_DIR':str(work),'MINI_EVAL_OUTPUT_DIR':str(output),**(env or {})},
            '_resolved_cwd':str(tmp_path)}
    path=tmp_path/'input.json';path.write_text(json.dumps(config))
    result=subprocess.run([sys.executable,'-m','runner.cs_entry'],cwd=Path.cwd(),env={**os.environ,'PYTHONPATH':str(Path.cwd()),'MINI_EVAL_INPUT':str(path),
            'MINI_EVAL_WORK_DIR':str(work),'MINI_EVAL_OUTPUT_DIR':str(output)},capture_output=True,text=True,timeout=10)
    events=[json.loads(line) for line in (work/'episodes.jsonl').read_text().splitlines()] if (work/'episodes.jsonl').exists() else []
    return result,events,work,output


def test_host_direct_starts_each_episode_with_params_and_aggregates(tmp_path):
    script=tmp_path/'business.py'
    script.write_text('import json,os\nfrom pathlib import Path\n'
        'c=json.load(open(os.environ["MINI_EVAL_INPUT"]))\n'
        'assert c["episodes"]==1 and c["business"]["scene"]=="test"\n'
        'assert c["seed"]==int(os.environ["MINI_EVAL_EPISODE_SEED"])\n'
        'Path(os.environ["MINI_EVAL_OUTPUT_DIR"]).joinpath("seen").write_text(os.environ["MINI_EVAL_EPISODE_ID"])\n'
        'print(json.dumps({"final":{"success_rate":int(os.environ["MINI_EVAL_EPISODE_ID"])%2,"episode_count":1,"runtime":0.01}}))\n')
    result,events,work,output=client(tmp_path,script)
    assert result.returncode==0,result.stderr
    assert [e['status'] for e in events]==['STARTED','COMPLETED']*3
    assert [e['seed'] for e in events if e['status']=='COMPLETED']==[7,8,9]
    assert [e['success'] for e in events if e['status']=='COMPLETED']==[True,False,True]
    assert [output.joinpath('episode-'+str(i),'seen').read_text() for i in range(1,4)]==['1','2','3']
    assert json.loads(result.stdout.splitlines()[-1])['final']['success_rate']==pytest.approx(2/3)


@pytest.mark.parametrize('behavior',['missing','invalid','exit'])
def test_host_direct_stops_after_bad_episode(tmp_path,behavior):
    script=tmp_path/'business.py'
    script.write_text('import json,os,sys\nfrom pathlib import Path\n'
        'i=int(os.environ["MINI_EVAL_EPISODE_ID"])\n'
        'Path(os.environ["MINI_EVAL_OUTPUT_DIR"]).joinpath("seen").write_text(str(i))\n'
        'if i==2:\n'
        +({'missing':'    sys.exit(0)\n','invalid':'    print(json.dumps({"final":{"episode_count":2,"success_rate":1}}));sys.exit(0)\n',
           'exit':'    sys.exit(7)\n'}[behavior])+
        'print(json.dumps({"final":{"episode_count":1,"success_rate":1,"runtime":0.01}}))\n')
    result,events,_,output=client(tmp_path,script)
    assert result.returncode!=0
    assert [e['status'] for e in events]==['STARTED','COMPLETED','STARTED','ERROR']
    assert not output.joinpath('episode-3').exists()
    assert events[-1]['exit_code']==(7 if behavior=='exit' else 0)
    assert events[1]['exit_code']==0


def test_host_ordinary_command_repeats_without_business_result(tmp_path):
    script=tmp_path/'command.py'
    script.write_text('import json,os\nfrom pathlib import Path\n'
        'c=json.load(open(os.environ["MINI_EVAL_INPUT"]))\n'
        'assert c["business"]["scene"]=="test"\n'
        'Path(os.environ["MINI_EVAL_OUTPUT_DIR"]).joinpath("seen").write_text(os.environ["MINI_EVAL_EPISODE_ID"])\n')
    result,events,_,output=client(tmp_path,script,ordinary=True)
    assert result.returncode==0,result.stderr
    assert [e['status'] for e in events]==['STARTED','COMPLETED']*3
    assert [output.joinpath('episode-'+str(i),'seen').read_text() for i in range(1,4)]==['1','2','3']


def test_host_sdk_command_uses_business_environment_per_episode(tmp_path):
    script=tmp_path/'sdk_business.py'
    script.write_text('import json,os\nfrom pathlib import Path\n'
        'assert os.environ["BUSINESS_FLAG"]=="from-login"\n'
        'assert os.environ["MINI_EVAL_EPISODE_ID"] in ("1","2")\n'
        'Path(os.environ["MINI_EVAL_RESULT"]).write_text(json.dumps({"success":True,"metrics":{},"artifacts":[]}))\n')
    result,events,_,_=client(tmp_path,script,episodes=2,env={'BUSINESS_FLAG':'from-login'},
        template_extra={'integration_mode':'sdk-command'})
    assert result.returncode==0,result.stderr
    assert [e['status'] for e in events]==['STARTED','COMPLETED']*2


def test_host_ordinary_single_episode_preserves_no_evaluation_metrics(tmp_path):
    script=tmp_path/'command.py';script.write_text('print("done")')
    result,events,_,_=client(tmp_path,script,episodes=1,ordinary=True)
    assert result.returncode==0,result.stderr
    assert [e['status'] for e in events]==['STARTED','COMPLETED']
    assert json.loads(result.stdout.splitlines()[-1])['final']['episode_count']==1


def test_worker_to_host_episode_preserves_business_argv_env_and_input(tmp_path):
    script=tmp_path/'mapped.py'
    script.write_text('import json,os,sys\n'
        'c=json.load(open(os.environ["MINI_EVAL_INPUT"]))\n'
        'assert c["business"]["scene"]=="test" and c["task"]["seed"]==int(os.environ["MINI_EVAL_EPISODE_SEED"])\n'
        'assert sys.argv[1:]==["--scene","test","--task","case"]\n'
        'assert os.environ["BUSINESS_FLAG"]=="from-login"\n'
        'print(json.dumps({"final":{"success_rate":1,"episode_count":1,"runtime":0.1}}))\n')
    template={'parameter_schema':{'version':1,'fields':{'scene':{'type':'string','binding':{'target':'argv','name':'--scene'}}}},
              'platform_bindings':[{'source':'task.task_name','target':'argv','name':'--task'}]}
    result,events,_,_=client(tmp_path,script,episodes=2,env={'BUSINESS_FLAG':'from-login'},template_extra=template)
    assert result.returncode==0,result.stderr
    assert [e['status'] for e in events]==['STARTED','COMPLETED']*2


def test_docker_entry_does_not_use_host_episode_adapter(tmp_path):
    script=tmp_path/'command.py';script.write_text('print("once")')
    result,events,_,_=client(tmp_path,script,episodes=3,ordinary=True,mode='docker')
    assert result.returncode==0,result.stderr
    assert result.stdout.count('once')==1
    assert events==[]


@pytest.mark.parametrize('policy',['stop','continue'])
def test_host_command_template_accepts_multiple_episodes_but_docker_does_not(env,policy):
    _,client,headers=env
    host={'name':'host-repeat','version':'1','image':'','execution_mode':'host',
          'task_type':'command','command':['/bin/true'],
          'host_runtime':{'environment_name':'approved','episode_launch':'per_episode','episode_error_policy':policy}}
    response=client.post('/api/v1/admin/client-templates',headers=headers['admin'],json=host)
    assert response.status_code==201,response.text
    assert response.json()['episode_reporting'] is True
    assert response.json()['host_runtime'].get('episode_error_policy','stop')==policy
    body={'title':'repeat','benchmark':'cs-client','model_ref':'command','episodes':3,'pool_id':'mock',
          'client':{'template_id':response.json()['id'],'service_mode':'none','task_name':'repeat'}}
    accepted=client.post('/api/v1/jobs',headers=headers['alice'],json=body)
    assert accepted.status_code==201,accepted.text
    docker=client.post('/api/v1/admin/client-templates',headers=headers['admin'],json={
        'name':'docker-once','version':'1','image':'approved:cs','task_type':'command','command':['/bin/true']})
    assert docker.status_code==201,docker.text
    body['client']['template_id']=docker.json()['id']
    rejected=client.post('/api/v1/jobs',headers=headers['alice'],json=body)
    assert rejected.status_code==400 and 'episodes=1' in rejected.text


def test_host_backend_keeps_login_values_in_business_only(tmp_path,monkeypatch):
    from backend.tests.test_host_backend import worker
    from worker.host_backend import HostBackend
    script=tmp_path/'business.py';script.write_text('print("ok")')
    w=worker(tmp_path,['python3','business.py'])
    task=w.state['task'];task['client_spec']['template']['host_runtime']['environment_source']='login'
    monkeypatch.setattr('worker.host_backend.probe',lambda *a,**k:None)
    monkeypatch.setattr('worker.host_identity.login_environment',lambda account:{
        'PATH':'/custom/bin','PYTHONPATH':'/business/lib','MY_FLAG':'yes','CUDA_VISIBLE_DEVICES':'wrong','MINI_EVAL_INPUT':'wrong'})
    backend=HostBackend(w);backend.create(task)
    config=json.loads((w.workspace/'runs'/task['execution_id']/'input.json').read_text())
    launch=json.loads((backend.root/'launch.json').read_text())
    business=config['_resolved_environment']
    assert business['MY_FLAG']=='yes' and business['CUDA_VISIBLE_DEVICES']==''
    assert business['MINI_EVAL_INPUT']!= 'wrong'
    assert '/business/lib' in business['PYTHONPATH']
    assert 'MY_FLAG' not in launch['environment']


def test_api_requires_all_host_command_episode_records_before_completion(env):
    main,client,headers=env
    _,token,_,job_id,task=prepare(env)
    with main.SessionLocal() as session:
        job=session.get(Job,job_id)
        job.client_spec={**job.client_spec,'template':{**job.client_spec['template'],
            'task_type':'command','execution_mode':'host','host_runtime':{'episode_launch':'per_episode'},
            'episode_reporting':True}}
        session.commit()
    base='/api/v1/worker/executions/'+task['execution_id']
    assert client.post(base+'/complete',headers=token,json={'status':'SUCCEEDED'}).status_code==400
    for i in (1,2):
        start={'episode_id':i,'seed':6+i,'status':'STARTED'}
        end={**start,'status':'COMPLETED','success':True,'metrics':{},'runtime':0.1,'artifacts':[]}
        assert client.post(base+'/episodes',headers=token,json=start).status_code==200
        assert client.post(base+'/episodes',headers=token,json=end).status_code==200
    assert client.post(base+'/complete',headers=token,json={'status':'SUCCEEDED'}).status_code==200
    detail=client.get('/api/v1/jobs/'+job_id,headers=headers['alice']).json()
    assert detail['status']=='SUCCEEDED' and detail['attempts'][0]['metrics'] is None


@pytest.mark.parametrize('behavior',['exit','missing','invalid'])
@pytest.mark.parametrize('failed_episode',[1,3])
def test_continue_direct_records_errors_and_attempts_all(tmp_path,behavior,failed_episode):
    script=tmp_path/'business.py'
    failure={'exit':'sys.exit(1)','missing':'sys.exit(0)','invalid':'print(json.dumps({"final":{}}));sys.exit(0)'}[behavior]
    script.write_text('import os,sys,json\ni=int(os.environ["MINI_EVAL_EPISODE_ID"])\n'
                      +f'if i=={failed_episode}: {failure}\n'
                      +'print(json.dumps({"final":{"episode_count":1,"success_rate":1,"runtime":0.01}}))\n')
    result,events,_,_=client(tmp_path,script,template_extra={'host_runtime':{'episode_launch':'per_episode','episode_error_policy':'continue'}})
    assert result.returncode!=0
    ends=[e for e in events if e['status']!='STARTED']
    assert [e['episode_id'] for e in ends]==[1,2,3]
    assert ends[failed_episode-1]['status']=='ERROR'
    assert ends[failed_episode-1]['exit_code']==(1 if behavior=='exit' else 0)
    assert sum(e['status']=='COMPLETED' for e in ends)==2
    assert '1 of 3 business episodes' in result.stderr


@pytest.mark.parametrize('integration',['ordinary','sdk-command'])
def test_continue_ordinary_and_sdk_commands(tmp_path,integration):
    script=tmp_path/'business.py'
    script.write_text('import os,sys,json\nfrom pathlib import Path\n'
                      'i=int(os.environ["MINI_EVAL_EPISODE_ID"])\n'
                      'if i==1: sys.exit(1)\n'
                      'Path(os.environ["MINI_EVAL_RESULT"]).write_text(json.dumps({"success":True}))\n')
    result,events,_,_=client(tmp_path,script,ordinary=integration=='ordinary',template_extra={
        'integration_mode':'direct' if integration=='ordinary' else 'sdk-command',
        'host_runtime':{'episode_launch':'per_episode','episode_error_policy':'continue'}})
    assert result.returncode!=0
    assert [e['status'] for e in events]==['STARTED','ERROR','STARTED','COMPLETED','STARTED','COMPLETED']
    assert [e['exit_code'] for e in events if e['status']!='STARTED']==[1,0,0]


def test_episode_policy_validation_and_old_worker_gate():
    from runner.template_contract import host_config
    from backend.app.scheduling import runtime_mismatch
    base={'environment_name':'python','episode_launch':'per_episode'}
    assert host_config(base).get('episode_error_policy','stop')=='stop'
    assert 'episode_error_policy' not in host_config({**base,'episode_error_policy':'stop'})
    assert host_config({**base,'episode_error_policy':'continue'})['episode_error_policy']=='continue'
    for bad in ({**base,'episode_error_policy':'ignore'}, {'environment_name':'python','episode_error_policy':'continue'}):
        with pytest.raises(ValueError):host_config(bad)
    job=SimpleNamespace(benchmark='cs-client',client_spec={'template':{'execution_mode':'host','host_runtime':{**base,'episode_error_policy':'continue'}}})
    assert '协议 17' in runtime_mismatch(SimpleNamespace(protocol_version=16),job)
    from backend.app.host_environments import blockers as host_blockers
    from backend.app.image_runtime import blockers as image_blockers
    for check in (host_blockers,image_blockers):
        assert '协议 17' in check(None,SimpleNamespace(protocol_version=16),job.client_spec,None)[0]
    assert runtime_mismatch(SimpleNamespace(protocol_version=17,execution_modes=['host']),job) is None


def test_continue_does_not_swallow_setup_or_cancellation(tmp_path):
    from runner.sdk import EvaluationClient,run
    class Failing(EvaluationClient):
        calls=0
        def setup(self,context):raise RuntimeError('setup failed')
        def run_episode(self,*args):self.calls+=1;raise KeyboardInterrupt()
    config={'episodes':3,'seed':1,'template':{'execution_mode':'host','host_runtime':{'episode_launch':'per_episode','episode_error_policy':'continue'}}}
    instance=Failing()
    with pytest.raises(RuntimeError,match='setup failed'):run(instance,config,tmp_path)
    assert instance.calls==0
    instance.setup=lambda context:None
    with pytest.raises(KeyboardInterrupt):run(instance,config,tmp_path)
    assert instance.calls==1


def test_continue_all_episodes_error_without_success_final(tmp_path):
    script=tmp_path/'fail.py';script.write_text('raise SystemExit(1)')
    result,events,_,_=client(tmp_path,script,ordinary=True,template_extra={
        'host_runtime':{'episode_launch':'per_episode','episode_error_policy':'continue'}})
    assert result.returncode!=0
    assert [e['episode_id'] for e in events if e['status']=='ERROR']==[1,2,3]
    assert '3 of 3 business episodes' in result.stderr
    assert '"final"' not in result.stdout
