"""Exercise actual runner functions, keeping business subprocesses real for SDK results."""
import json
from pathlib import Path
import subprocess
import sys

import pytest
from runner import command_sdk, cs_entry, service_entry
from runner.image_runtime import validate
from runner.episode_contract import EpisodeExecutionError, result_error_code


@pytest.fixture
def execution(tmp_path,monkeypatch):
    work=tmp_path/'work';output=tmp_path/'output';work.mkdir();output.mkdir()
    inp=tmp_path/'input.json'
    for key,value in {'MINI_EVAL_INPUT':inp,'MINI_EVAL_WORK_DIR':work,'MINI_EVAL_OUTPUT_DIR':output}.items():monkeypatch.setenv(key,str(value))
    config={'contract_version':1,'task_name':'scene','episodes':2,'seed':7,'endpoint':'http://127.0.0.1:9000','service_mode':'none','request_timeout_seconds':1,
            'parameters':{'scene':'kitchen'},'port':19000,'bind_host':'127.0.0.1',
            'template':{'execution_mode':'docker','integration_mode':'direct','command':['/bin/echo','literal ; argument'],
                        'runtime':validate({'command_mode':'exec','working_directory':str(tmp_path),'work_dir':str(work),'output_dir':str(output)}),
                        'parameter_schema':{'version':1,'fields':{'scene':{'type':'string','binding':{'target':'env','name':'SCENE'}}}},
                        'platform_bindings':[{'source':'task.task_name','target':'argv','name':'--task'}]}}
    return inp,config,work,output


@pytest.mark.parametrize('entry',[cs_entry,service_entry])
@pytest.mark.parametrize('mode',['host','docker'])
@pytest.mark.parametrize('exit_code',[0,7])
def test_final_argv_environment_cwd_and_exit_are_preserved(execution,monkeypatch,entry,mode,exit_code):
    inp,config,work,output=execution
    if entry is service_entry:config['workload']='service'
    if mode=='host':
        config['template'].update(execution_mode='host',runtime=None,host_runtime={'command_mode':'exec','environment_name':'test'})
        config.update(_resolved_command=config['template']['command'],_resolved_environment={},_resolved_cwd=str(inp.parent))
    inp.write_text(json.dumps(config));seen=[]
    monkeypatch.setattr('subprocess.call',lambda argv,**kwargs:seen.append((argv,kwargs)) or exit_code)
    with pytest.raises(SystemExit) as error:entry.main()
    assert error.value.code==exit_code
    argv,kwargs=seen[0]
    assert argv==['/bin/echo','literal ; argument','--task','scene']
    assert kwargs['env']['SCENE']=='kitchen' and kwargs['cwd']==str(inp.parent)
    assert json.loads(Path(kwargs['env']['MINI_EVAL_INPUT']).read_text())['business']=={'scene':'kitchen'}
    assert kwargs['env']['MINI_EVAL_OUTPUT_DIR']==str(output)


@pytest.mark.parametrize('fault',['version','missing-program','business-conflict','platform-conflict','missing-input'])
def test_client_rejects_invalid_startup_without_running_business(execution,monkeypatch,fault,capsys):
    inp,config,_,_=execution
    if fault=='version':config['contract_version']=999
    if fault=='missing-program':config['template']['command']=['/definitely/not/an/executable']
    if fault=='business-conflict':config['environment']={'SCENE':'shadow'};config['template']['runtime']['overridable_env']=['SCENE']
    if fault=='platform-conflict':
        config['template']['platform_bindings']=[{'source':'task.task_name','target':'env','name':'TASK'}]
        config['environment']={'TASK':'shadow'};config['template']['runtime']['overridable_env']=['TASK']
    if fault!='missing-input':inp.write_text(json.dumps(config))
    monkeypatch.setattr('subprocess.call',lambda *a,**k:pytest.fail('invalid business must not start'))
    with pytest.raises(SystemExit) as error:cs_entry.main()
    assert error.value.code==2 and 'CS client execution or result processing failed' in capsys.readouterr().err


@pytest.mark.parametrize('behavior',['success','false','missing','nonbool','unknown','large','symlink','exit'])
def test_sdk_actual_business_result_and_error_events(execution,behavior):
    inp,config,work,output=execution
    body={
        'success':"p.write_text('{\"success\":true}')",'false':"p.write_text('{\"success\":false}')",
        'missing':'pass','nonbool':"p.write_text('{\"success\":1}')",'unknown':"p.write_text('{\"success\":true,\"extra\":1}')",
        'large':"p.write_text('x'*16385)", 'symlink':"p.symlink_to('/dev/null')",'exit':'sys.exit(3)',
    }[behavior]
    script=inp.parent/'business.py';script.write_text('import os,sys\nfrom pathlib import Path\np=Path(os.environ["MINI_EVAL_RESULT"])\n'+body+'\n')
    config['template']={'command':[sys.executable,str(script)],'integration_mode':'sdk-command'}
    inp.write_text(json.dumps(config))
    if behavior in ('success','false'):
        command_sdk.main();report=json.loads((output/'report.json').read_text())
        assert report['metrics']['success_rate']==(1 if behavior=='success' else 0)
    else:
        with pytest.raises(subprocess.CalledProcessError if behavior=='exit' else EpisodeExecutionError):command_sdk.main()
        assert not (output/'report.json').exists()
    events=[json.loads(line) for line in (work/'episodes.jsonl').read_text().splitlines()]
    assert events[0]['status']=='STARTED'
    assert events[-1]['status']==('COMPLETED' if behavior in ('success','false') else 'ERROR')
    assert events[0]['seed']==7

    if behavior not in ('success','false','exit'):
        assert events[-1]['exit_code']==0
        assert result_error_code(events[-1]['error'])==('missing_result_file' if behavior=='missing' else 'invalid_result_file')


@pytest.mark.parametrize('mode',['host','docker'])
def test_client_output_alias_matches_published_input_and_mapping(execution,monkeypatch,mode):
    inp,config,_,_=execution
    actual=inp.parent/'business-output';actual.mkdir()
    config['template']['platform_bindings']=[{'source':'platform.paths.output','target':'argv','name':'--output-dir'}]
    if mode=='docker':config['template']['runtime']['output_dir']=str(actual)
    else:
        config['template'].update(execution_mode='host',runtime=None)
        config['_resolved_environment']={'MINI_EVAL_OUTPUT_DIR':str(actual)}
    inp.write_text(json.dumps(config));seen=[]
    monkeypatch.setattr('subprocess.call',lambda argv,**kw:seen.append((argv,kw)) or 0)
    with pytest.raises(SystemExit) as error:cs_entry.main()
    assert error.value.code==0
    argv,kwargs=seen[0]
    assert argv[-2:]==['--output-dir',str(actual)]
    assert kwargs['env']['MINI_EVAL_OUTPUT_DIR']==str(actual)
    published=json.loads(Path(kwargs['env']['MINI_EVAL_INPUT']).read_text())
    assert published['platform']['paths']['output']==str(actual)


@pytest.mark.parametrize('behavior',['invalid-json','directory','invalid-artifacts'])
def test_sdk_independent_root_rejects_invalid_result(execution,behavior):
    inp,config,work,output=execution
    body={'invalid-json':"p.write_text('{')",'directory':'p.mkdir()',
          'invalid-artifacts':"p.write_text('{\"success\":true,\"artifacts\":[\"../escape\"]}')"}[behavior]
    script=inp.parent/'invalid-business.py'
    script.write_text('import os\nfrom pathlib import Path\np=Path(os.environ["MINI_EVAL_RESULT"])\n'+body+'\n')
    config.update(_artifact_layout='batch-v1',_artifact_root=str(output),episodes=1)
    config['template']={'command':[sys.executable,str(script)],'integration_mode':'sdk-command'}
    inp.write_text(json.dumps(config))
    with pytest.raises(EpisodeExecutionError):command_sdk.main()
    events=[json.loads(line) for line in (work/'episodes.jsonl').read_text().splitlines()]
    assert events[-1]['status']=='ERROR' and events[-1]['exit_code']==0
    assert result_error_code(events[-1]['error'])=='invalid_result_file'
