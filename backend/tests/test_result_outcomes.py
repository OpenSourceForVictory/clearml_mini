"""Real child output, independent outcome accounting, and report consistency."""
import csv
import io
from types import SimpleNamespace as NS

import pytest
from backend.tests.test_execution import env
from backend.tests.test_host_shell_episode import client
from backend.tests.test_monitoring import batch
from backend.app.core import Job, Run, EpisodeRecord
from backend.app.scheduling import transition
from backend.app.result_views import summarize_attempt, episode_view, aggregate, process_state
from backend.app.reports import compare
from runner.episode_contract import EpisodeResultError, result_error_code


@pytest.mark.parametrize('body,reason',[
    ('pass','missing_final'),
    ('print("INFO: {\\"final\\":{}}")','missing_final'),
    ('import sys; print(\'{"final":{}}\',file=sys.stderr)','missing_final'),
    ('print(\'{"final":{}}\');print(\'{"final":{}}\')','multiple_final'),
    ('print(\'{"final":null}\')','invalid_final'),
    ('print(\'{"final":{}}\')','invalid_final'),
    ('print(\'{"final":{"episode_count":1,"success_rate":0.5,"runtime":1}}\')','invalid_final'),
])
def test_zero_exit_bad_result_is_specific_and_controlled(tmp_path,body,reason):
    script=tmp_path/'business.py';script.write_text(body)
    result,events,_,_=client(tmp_path,script,template_extra={'host_runtime':{'episode_launch':'per_episode','episode_error_policy':'continue'}})
    errors=[e for e in events if e['status']=='ERROR']
    assert len(errors)==3 and all(e['exit_code']==0 for e in errors)
    assert all(result_error_code(e['error'])==reason for e in errors)
    assert 'Traceback' not in result.stderr and 'startup failed' not in result.stderr
    assert result.returncode!=0 and 'remaining episodes were attempted' in result.stderr


def template(**updates):
    return dict(execution_mode='host',integration_mode='direct',task_type='evaluation',
                host_runtime={'episode_launch':'per_episode'},**updates)


def row(result=None,started=True):
    return NS(result=result,started={'episode_id':1} if started else None)


def summary(t,rows,status='FAILED',code=2,episodes=3):
    return summarize_attempt(NS(client_spec={'template':t},episodes=episodes),NS(status=status,exit_code=code),rows)


def test_execution_and_result_are_orthogonal():
    rows=[row({'status':'ERROR','exit_code':0,'error':str(EpisodeResultError('missing_final'))}) for _ in range(3)]
    value=summary(template(),rows)
    assert value['execution']==dict(normal=3,abnormal=0,running=0,unknown=0,not_started=0)
    assert value['protocol']['invalid']==3 and value['protocol']['success_rate'] is None
    assert value['wrapper_exit_code']==2
    native=template();native['host_runtime']={'episode_launch':'once'}
    assert episode_view(native,rows[0],'FAILED')['execution_state']=='not_applicable'
    value=summary(native,rows)
    assert value['execution']['unknown']==1 and value['execution']['abnormal']==0
    assert summary(native,rows,code=0)['execution']['normal']==1


@pytest.mark.parametrize('business,state', [
    ({'kind':'task','success':True,'metrics':{}},'success'),
    ({'kind':'task','success':False,'metrics':{}},'failure'),
    (None,'unreported'),
])
def test_task_level_result_is_separate_from_command_exit(business,state):
    template={'task_type':'command','integration_mode':'sdk-task-result'}
    run=NS(status='SUCCEEDED',exit_code=0,result_json=business)
    job=NS(client_spec={'template':template},episodes=1)
    value=summarize_attempt(job,run,[])
    assert value['execution']['normal']==1
    assert value['task_result_state']==state
    assert value['protocol']['not_applicable_tasks']==1


@pytest.mark.parametrize('code,started,ended,terminal,expected',[
    (0,True,True,True,'normal'),(-9,True,True,True,'abnormal'),(None,True,False,False,'running'),
    (None,True,False,True,'unknown'),(None,False,True,True,'unknown'),(None,False,False,True,'not_started'),
])
def test_exit_states(code,started,ended,terminal,expected):
    assert process_state(code,started,ended,terminal)==expected


def test_mixed_results_and_commands():
    t=template()
    result=summary(t,[row({'status':'COMPLETED','success':False,'exit_code':0}),row({'status':'ERROR','error':'old generic','exit_code':7}),row()],status='RUNNING')
    assert result['execution']['normal']==1 and result['execution']['abnormal']==1 and result['execution']['running']==1
    assert result['protocol']['failure']==1 and result['protocol']['errors']==1 and result['protocol']['unreported']==1
    t['task_type']='command'
    cmd=summary(t,[row({'status':'COMPLETED','success':True,'exit_code':0})])
    both=aggregate([result,cmd]);assert both['protocol']['planned']==3 and both['protocol']['not_applicable_tasks']==1
    assert both['protocol']['success_rate']==0 and both['execution']['normal']==2
    assert aggregate([])['protocol']['success_rate'] is None
    assert result_error_code('episode_result_missing_final: secret') is None
    assert episode_view(t,None,'CANCELLED')['protocol_state']=='not_applicable'


def test_batch_report_episode_and_csv_share_latest_statistics(env):
    main,c,auth=env;b,_=batch(env)
    item=b['cases'][0]
    with main.SessionLocal() as s:
        j=s.get(Job,item['job_id']);j.episodes=101
        j.client_spec={**j.client_spec,'template':{**j.client_spec['template'],**template(),'episode_reporting':True}}
        run=s.get(Run,item['run_id']);transition(s,run,'FAILED');run.exit_code=2;run.cleanup_confirmed=True
        for i in range(1,4):
            s.add(EpisodeRecord(run_id=run.id,episode_id=i,seed=7+i-1,started={'episode_id':i},result={'status':'ERROR','exit_code':0,'error':str(EpisodeResultError('missing_final'))}))
        s.commit()
    monitor=c.get(f'/api/v1/batches/{b["id"]}/monitor',headers=auth['alice']).json()
    report=c.get(f'/api/v1/batches/{b["id"]}/report',headers=auth['alice']).json()
    assert report['outcomes']==monitor['outcomes']
    assert report['outcomes']['execution']['normal']==3 and report['outcomes']['protocol']['invalid']==3
    path=f'/api/v1/jobs/{item["job_id"]}/attempts/{item["run_id"]}/episodes?include_planned=true'
    first=c.get(path,headers=auth['alice']).json();last=c.get(path+'&page=2',headers=auth['alice']).json()
    assert first['outcomes']==last['outcomes']==monitor['cases'][0]['outcomes']
    assert first['items'][0]['execution_state']=='normal' and first['items'][0]['protocol_state']=='invalid'
    data=c.get(f'/api/v1/batches/{b["id"]}/report?format=csv',headers=auth['alice']).content.decode('utf-8-sig')
    csvrow=next(csv.DictReader(io.StringIO(data)))
    assert csvrow['program_normal']=='3' and csvrow['protocol_invalid']=='3'
    assert c.post(f'/api/v1/jobs/{item["job_id"]}/retry',headers=auth['alice']).status_code==202
    latest=c.get(f'/api/v1/batches/{b["id"]}/monitor',headers=auth['alice']).json()
    assert latest['outcomes']['execution']['normal']==0 and latest['outcomes']['protocol']['invalid']==0
    assert c.get(path,headers=auth['alice']).json()['outcomes']==first['outcomes']


def test_command_comparison_never_subtracts_null():
    item=dict(case_id='command',task_name='sample',seed=1,episodes=3,parameters_digest='same',config={},episode_reporting=True,partial=False,success_rate=None,task_type='command')
    report=dict(cases=[item],summary={'success_rate':None})
    value=compare(report,report)
    assert not value['comparable'] and value['delta_success_rate'] is None
    assert value['cases'][0]['reasons']==['protocol_not_applicable']
    item['task_type']='evaluation'
    assert compare(report,report)['cases'][0]['reasons']==['missing_valid_results']


@pytest.mark.parametrize('behavior,expected',[
    ('good',None),('missing','missing_final'),('duplicate','multiple_final'),
    ('null','invalid_final'),('fields','invalid_final'),('nonzero','process'),
])
def test_adapter_real_child_under_parent_coverage(tmp_path,behavior,expected):
    import sys
    from runner.host_episode import HostEpisodeClient
    from runner.sdk import Context,Episode
    import json
    valid='print(\'{"final":{"episode_count":1,"success_rate":1,"runtime":0.1}}\')'
    source={'good':valid,'missing':'print("normal log")','duplicate':valid+';'+valid,
            'null':'print(\'{"final":null}\')','fields':'print(\'{"final":{}}\')','nonzero':'raise SystemExit(7)'}[behavior]
    work=tmp_path/'work';work.mkdir()
    env={'MINI_EVAL_WORK_DIR':str(work)}
    task={'template':template(),'task_name':'demo','episodes':1,'seed':7,'endpoint':'','parameters':{}}
    adapter=HostEpisodeClient([sys.executable,'-c',source],str(tmp_path),env,False)
    context=Context(task,tmp_path/'output',_episode_output_dir=tmp_path/'output'/'episode-1',_episode_id=1)
    if expected=='process':
        with pytest.raises(RuntimeError,match='code 7'):adapter.run_episode(task,Episode(1,7),context)
        assert adapter.process_exit_code==7
    elif expected:
        with pytest.raises(EpisodeResultError) as error:adapter.run_episode(task,Episode(1,7),context)
        assert error.value.code==expected and adapter.process_exit_code==0
    else:
        assert adapter.run_episode(task,Episode(1,7),context).success
        assert adapter.process_exit_code==0


def test_command_comparison_api_handles_not_applicable(env):
    from backend.tests.test_preflight import setup
    main,c,auth=env
    body,_=setup(env)
    batches=[c.post('/api/v1/batches',headers=auth['alice'],json=body).json() for _ in range(2)]
    with main.SessionLocal() as s:
        for b in batches:
            for item in b['cases']:
                j=s.get(Job,item['job_id']);j.client_spec={**j.client_spec,'template':{**j.client_spec['template'],'task_type':'command','episode_reporting':True}}
                run=s.get(Run,item['run_id']);transition(s,run,'SUCCEEDED');run.exit_code=0
                for i in range(j.episodes):s.add(EpisodeRecord(run_id=run.id,episode_id=i+1,seed=i,started={'episode_id':i+1},result={'status':'COMPLETED','success':True,'exit_code':0}))
        s.commit()
    response=c.get('/api/v1/batch-comparisons',params={'left_id':batches[0]['id'],'right_id':batches[1]['id']},headers=auth['alice'])
    assert response.status_code==200,response.text
    result=response.json();assert result['delta_success_rate'] is None and not result['comparable']
    assert all('protocol_not_applicable' in row['reasons'] for row in result['cases'])


def test_sdk_continue_preserves_three_zero_exits_without_traceback(tmp_path,monkeypatch,capsys):
    import sys,json
    from runner.host_episode import HostEpisodeClient
    from runner.sdk import run
    from runner.episode_contract import EpisodeExecutionError
    work=tmp_path/'work';work.mkdir();monkeypatch.setenv('MINI_EVAL_WORK_DIR',str(work))
    t=template();t['host_runtime']['episode_error_policy']='continue'
    task={'template':t,'episodes':3,'seed':7,'task_name':'case','endpoint':'','parameters':{}}
    adapter=HostEpisodeClient([sys.executable,'-c','print("business completed")'],str(tmp_path),{'MINI_EVAL_WORK_DIR':str(work)},False)
    with pytest.raises(EpisodeExecutionError,match='3 of 3'):
        run(adapter,task,tmp_path/'output')
    events=[json.loads(line) for line in (work/'episodes.jsonl').read_text().splitlines()]
    assert [e['status'] for e in events]==['STARTED','ERROR']*3
    assert all(e['exit_code']==0 and result_error_code(e['error'])=='missing_final' for e in events if e['status']=='ERROR')
    assert 'Traceback' not in capsys.readouterr().err


@pytest.mark.parametrize('mode,kind,expected',[
    ('once','command','execution'),('per_episode','command','episodes'),
    ('once','evaluation','episodes'),('per_episode','evaluation','episodes'),
])
def test_detail_mode_uses_process_contract_not_reporting(mode,kind,expected):
    t=template();t.update(task_type=kind,episode_reporting=True,host_runtime={'episode_launch':mode})
    value=summary(t,[],code=7)
    assert value['detail_mode']==expected
    assert sum(value['execution'].values())==(1 if mode=='once' else 3)
    if expected=='execution':
        assert value['execution']['abnormal']==1 and value['protocol']['planned']==0
    t['integration_mode']='sdk-command'
    assert summary(t,[])['detail_mode']=='episodes'


@pytest.mark.parametrize('status,code,expected',[
    ('SUCCEEDED',0,'normal'),('FAILED',7,'abnormal'),('TIMED_OUT',None,'unknown'),
    ('CANCELLED',None,'not_started'),('RUNNING',None,'running'),('FAILED',None,'unknown'),
])
def test_whole_command_lifecycle_and_exit(status,code,expected):
    t=template();t.update(task_type='command',host_runtime={'episode_launch':'once'})
    result=summary(t,[],status=status,code=code,episodes=10)
    assert result['execution'][expected]==1 and sum(result['execution'].values())==1
    assert result['protocol']['valid']==0


def test_whole_command_api_does_not_invent_rounds_preserves_history(env):
    main,c,auth=env;b,_=batch(env);item=b['cases'][0]
    with main.SessionLocal() as s:
        j=s.get(Job,item['job_id']);j.episodes=10
        j.client_spec={**j.client_spec,'template':{**template(),'task_type':'command','host_runtime':{'episode_launch':'once'},'episode_reporting':True}}
        run=s.get(Run,item['run_id']);transition(s,run,'FAILED');run.exit_code=7
        s.commit()
    path=f'/api/v1/jobs/{item["job_id"]}/attempts/{item["run_id"]}/episodes?include_planned=true'
    value=c.get(path,headers=auth['alice']).json()
    assert value['items']==[] and value['planned']==10
    assert value['outcomes']['detail_mode']=='execution'
    assert value['outcomes']['execution']['abnormal']==1
    assert c.get(path,headers=auth['bob']).status_code==404
    with main.SessionLocal() as s:
        s.add(EpisodeRecord(run_id=item['run_id'],episode_id=1,seed=7,started={'episode_id':1},result=None));s.commit()
    assert len(c.get(path,headers=auth['alice']).json()['items'])==1
