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
    b'\0MINI_EVAL_LOGIN_ENV\0=secret\0',
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
    with pytest.raises(RuntimeError,match='timed out after 60 seconds') as error:login_environment(account)
    assert account.pw_name in str(error.value)
    assert 'SECRET=' not in str(error.value)


def client(tmp_path,script,*,episodes=3,ordinary=False,mode='host',launch='per_episode',env=None,template_extra=None,timing=False):
    work=tmp_path/'work';work.mkdir();output=tmp_path/'output';output.mkdir()
    template={'execution_mode':mode,'integration_mode':'direct','task_type':'command' if ordinary else 'evaluation',
              'host_runtime':{'command_mode':'exec','episode_launch':launch},'command':[sys.executable,str(script)]}
    template.update(template_extra or {})
    config={'_episode_timing_v1':timing,'contract_version':1,'service_mode':'none','endpoint':'','task_name':'case','episodes':episodes,'seed':7,
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
    w.config['host_login_timeout_seconds']=90
    observed=[]
    def capture(account,timeout_seconds):
        observed.append(timeout_seconds)
        return {'PATH':'/custom/bin','PYTHONPATH':'/business/lib','MY_FLAG':'yes',
                'CUDA_VISIBLE_DEVICES':'wrong','MINI_EVAL_INPUT':'wrong'}
    monkeypatch.setattr('worker.host_identity.login_environment',capture)
    backend=HostBackend(w);backend.create(task)
    assert observed==[90]
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


@pytest.mark.parametrize('timeout',[1,60,90,300])
def test_login_environment_passes_node_timeout(monkeypatch,timeout):
    account=pwd.getpwuid(os.geteuid())
    def capture(*a,**kw):
        assert kw['timeout']==timeout
        return SimpleNamespace(returncode=0,stdout=b'\0MINI_EVAL_LOGIN_ENV\0OK=yes\0')
    monkeypatch.setattr('worker.host_identity.subprocess.run',capture)
    assert login_environment(account,timeout)['OK']=='yes'


@pytest.mark.parametrize('timeout',[0,-1,301,True,1.5,'60',None])
def test_login_environment_rejects_invalid_node_timeout(monkeypatch,timeout):
    def unexpected(*a,**kw):raise AssertionError('must not launch invalid configuration')
    monkeypatch.setattr('worker.host_identity.subprocess.run',unexpected)
    with pytest.raises(ValueError,match='host_login_timeout_seconds'):
        login_environment(pwd.getpwuid(os.geteuid()),timeout)


def test_login_environment_custom_timeout_redacts_output(monkeypatch):
    def timeout(*a,**kw):
        raise subprocess.TimeoutExpired('shell',kw['timeout'],output=b'SECRET=value',stderr=b'PRIVATE=value')
    monkeypatch.setattr('worker.host_identity.subprocess.run',timeout)
    with pytest.raises(RuntimeError,match='timed out after 90 seconds') as error:
        login_environment(pwd.getpwuid(os.geteuid()),90)
    assert 'SECRET' not in str(error.value) and 'PRIVATE' not in str(error.value)
    assert error.value.__suppress_context__


def test_login_environment_real_shell_takes_more_than_old_limit(tmp_path):
    # A real bounded subprocess, without editing the account's startup files.
    account=pwd.getpwuid(os.geteuid())
    shell=tmp_path/'slow-shell'
    shell.write_text('#!/bin/sh\nsleep 11\nexec /bin/bash "$@"\n')
    shell.chmod(0o700)
    test_account=SimpleNamespace(pw_shell=str(shell),pw_dir=str(tmp_path),
        pw_name=account.pw_name,pw_uid=account.pw_uid,pw_gid=account.pw_gid)
    assert login_environment(test_account)['HOME']==str(tmp_path)


def test_login_environment_real_bash_exported_modules(monkeypatch):
    # Generate actual Bash serialization without modifying the account's profile.
    result=subprocess.run(['/bin/bash','--noprofile','--norc','-c',
        "ml() { :; }; module() { :; }; export -f ml module; "
        "export LD_LIBRARY_PATH=/approved/cann/lib64; "
        "printf '\\0MINI_EVAL_LOGIN_ENV\\0'; /usr/bin/env -0"],
        env={'PATH':'/usr/bin:/bin'},capture_output=True,check=True,timeout=5)
    assert b'BASH_FUNC_ml%%=' in result.stdout
    assert b'BASH_FUNC_module%%=' in result.stdout
    monkeypatch.setattr('worker.host_identity.subprocess.run',lambda *a,**kw:result)
    values=login_environment(pwd.getpwuid(os.geteuid()))
    assert values['LD_LIBRARY_PATH']=='/approved/cann/lib64'
    assert values['PATH']=='/usr/bin:/bin'
    assert not any(key.startswith('BASH_FUNC_') for key in values)


@pytest.mark.parametrize('entry',[
    b'=secret', b'\xff=secret',
    b'VALUE='+b'x'*16385, b'VALUE=\xff',
    b'BASH_FUNC_module%%='+b'x'*131072,
])
def test_login_environment_function_filter_keeps_validation(monkeypatch,entry):
    result=SimpleNamespace(returncode=0,stdout=b'\0MINI_EVAL_LOGIN_ENV\0'+entry+b'\0',stderr=b'')
    monkeypatch.setattr('worker.host_identity.subprocess.run',lambda *a,**kw:result)
    with pytest.raises(RuntimeError,match='environment') as error:
        login_environment(pwd.getpwuid(os.geteuid()))
    assert 'secret' not in str(error.value)


def test_login_environment_function_filter_preserves_normal_names_and_boundary(monkeypatch):
    result=SimpleNamespace(returncode=0,stdout=b'\0MINI_EVAL_LOGIN_ENV\0'
        b'BASH_FUNC_label=ordinary\0BASH_FUNC_ml%%=() { :; }\0VALUE='+b'x'*16384+b'\0',stderr=b'')
    monkeypatch.setattr('worker.host_identity.subprocess.run',lambda *a,**kw:result)
    assert login_environment(pwd.getpwuid(os.geteuid()))=={
        'BASH_FUNC_label':'ordinary','VALUE':'x'*16384}


def test_login_environment_special_names_reach_real_child(monkeypatch):
    expected={'MY-FLAG':'yes','app.setting':'a=b','WITH SPACE':'literal',
              '变量':'值','OTHER%%':'ok','BASH_FUNC_module%':'ordinary',
              'SHELL_LITERAL':'$(echo should-not-run); $HOME', 'EMPTY':''}
    raw=b'\0MINI_EVAL_LOGIN_ENV\0'+b''.join(
        key.encode()+b'='+value.encode()+b'\0' for key,value in expected.items())
    real_run=subprocess.run
    with monkeypatch.context() as patch:
        patch.setattr('worker.host_identity.subprocess.run',lambda *a,**kw:SimpleNamespace(
            returncode=0,stdout=raw,stderr=b''))
        captured=login_environment(pwd.getpwuid(os.geteuid()))
    assert captured==expected
    result=real_run([sys.executable,'-c',
        'import json,os,sys; print(json.dumps({k:os.environ[k] for k in json.loads(sys.argv[1])}))',
        json.dumps(list(expected))],env=captured,capture_output=True,text=True,check=True,timeout=5)
    assert json.loads(result.stdout)==expected


@pytest.mark.parametrize('integration,ordinary',[('direct',False),('direct',True),('sdk-command',False)])
@pytest.mark.parametrize('target',['argv','env'])
def test_per_episode_dynamic_mapping_uses_child_input(tmp_path,integration,ordinary,target):
    script=tmp_path/'mapped.py'
    script.write_text('''import json,os,sys
from pathlib import Path
c=json.load(open(os.environ['MINI_EVAL_INPUT']))
i=int(os.environ['MINI_EVAL_EPISODE_ID'])
if len(sys.argv)>1:
    values=dict(zip(sys.argv[1::2],sys.argv[2::2]))
    n,seed,work,out=(values[k] for k in ('--count','--seed','--work','--out'))
else:
    n,seed,work,out=(os.environ[k] for k in ('COUNT','SEED','WORK','OUT'))
assert int(n)==c['episodes']==c['task']['episodes']==1,(n,c['episodes'])
assert int(seed)==c['seed']==7+i-1
assert work==c['platform']['paths']['work']==os.environ['MINI_EVAL_WORK_DIR']
assert out==c['platform']['paths']['output']==os.environ['MINI_EVAL_OUTPUT_DIR']
assert Path(work).name==Path(out).name=='episode-'+str(i)
assert c['business']['scene']=='test'
Path(out,'seen').write_text(str(i))
if 'MINI_EVAL_RESULT' in os.environ:
    Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({'success':True}))
print(json.dumps({'final':{'episode_count':1,'success_rate':1,'runtime':0.01}}),flush=True)
''')
    sources=['task.episodes','task.seed','platform.paths.work','platform.paths.output']
    names=['--count','--seed','--work','--out'] if target=='argv' else ['COUNT','SEED','WORK','OUT']
    extra={'integration_mode':integration,'platform_bindings':[{'source':s,'target':target,'name':n} for s,n in zip(sources,names)]}
    result,events,_,output=client(tmp_path,script,episodes=3,ordinary=ordinary,template_extra=extra)
    assert result.returncode==0,result.stderr
    assert [e['status'] for e in events]==['STARTED','COMPLETED']*3
    assert [e['episode_id'] for e in events if e['status']=='COMPLETED']==[1,2,3]
    assert [output.joinpath('episode-'+str(i),'seen').read_text() for i in (1,2,3)]==['1','2','3']


@pytest.mark.parametrize('mode,integration',[('host','direct'),('docker','direct'),('host','sdk-command'),('docker','sdk-command')])
def test_existing_whole_task_mapping_preserves_total(tmp_path,mode,integration):
    script=tmp_path/'whole.py'
    script.write_text('''import json,os,sys
from pathlib import Path
c=json.load(open(os.environ['MINI_EVAL_INPUT']))
assert sys.argv[1:]==['--count','3']
assert c['episodes']==c['task']['episodes']==3
if 'MINI_EVAL_RESULT' in os.environ:Path(os.environ['MINI_EVAL_RESULT']).write_text('{"success":true}')
print('{"final":{"episode_count":3,"success_rate":1,"runtime":0.01}}',flush=True)
''')
    result,_,_,_=client(tmp_path,script,episodes=3,mode=mode,launch='whole_task',template_extra={
        'integration_mode':integration,'platform_bindings':[{'source':'task.episodes','target':'argv','name':'--count'}]})
    assert result.returncode==0,result.stderr


@pytest.mark.parametrize('binding',['business','platform'])
def test_per_episode_environment_conflict_fails_before_business(tmp_path,binding):
    script=tmp_path/'must_not_run.py'
    script.write_text("from pathlib import Path\nPath('unexpected-launch').touch()\n")
    extra={'host_runtime':{'command_mode':'exec','episode_launch':'per_episode','environment':{'VALUE':'explicit'}}}
    if binding=='business':
        extra['parameter_schema']={'version':1,'fields':{'scene':{'type':'string','binding':{'target':'env','name':'VALUE'}}}}
    else:extra['platform_bindings']=[{'source':'task.episodes','target':'env','name':'VALUE'}]
    result,events,_,_=client(tmp_path,script,template_extra=extra)
    assert result.returncode!=0
    assert [e['status'] for e in events]==['STARTED','ERROR']
    assert not (tmp_path/'unexpected-launch').exists()
    assert 'environment conflicts' in result.stderr
