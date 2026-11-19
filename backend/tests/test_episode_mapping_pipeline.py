"""Worker preparation -> real business processes -> durable event upload."""
import json
import subprocess
from types import SimpleNamespace

import pytest

from backend.tests.test_host_backend import worker
from worker.host_backend import HostBackend
from worker.client import Worker


@pytest.mark.parametrize('integration',['direct','sdk-command','command'])
@pytest.mark.parametrize('protocol',[1,2])
@pytest.mark.parametrize('initial_seed',[0,42,2147483647])
def test_host_placeholders_and_mapping_agree_across_worker_runner(tmp_path,monkeypatch,integration,protocol,initial_seed):
    (tmp_path/'business.py').write_text('''import json,os,sys
from pathlib import Path
n,seed,inp,work,out=sys.argv[1:6]
c=json.load(open(inp))
i=int(os.environ['MINI_EVAL_EPISODE_ID'])
assert n==os.environ['COUNT']=='1'
assert int(seed)==c['seed']==c['task']['seed']==int(os.environ['MINI_EVAL_EPISODE_SEED'])
assert int(seed)==int(os.environ['PLACEHOLDER_SEED'])==int(os.environ['MAPPED_SEED'])
assert inp==os.environ['MINI_EVAL_INPUT']
assert work==os.environ['MINI_EVAL_WORK_DIR']==c['platform']['paths']['work']
assert out==os.environ['MINI_EVAL_OUTPUT_DIR']==c['platform']['paths']['output']
assert Path(work).name==Path(out).name=='episode-'+str(i)
assert sys.argv[6:]==['--fixed-seed','42','--count','1','--mapped-seed',seed]
Path(out,'observed.json').write_text(json.dumps({'episode':i,'seed':int(seed),'episodes':c['episodes']}))
Path(os.environ['MINI_EVAL_RESULT']).write_text('{"success":true}')
print('{"final":{"success_rate":1,"episode_count":1,"runtime":0.01}}',flush=True)
''')
    w=worker(tmp_path,['python3','business.py','${episodes}','${seed}','${input_path}','${work_dir}','${output_dir}','--fixed-seed','42'])
    task=w.state['task'];task['episodes']=10;spec=task['client_spec'];spec['service_mode']='none';spec['seed']=initial_seed
    template=spec['template'];template['integration_mode']='direct' if integration=='command' else integration;template['episode_reporting']=True
    if integration=='command':template['task_type']='command'
    template['host_runtime'].update(episode_launch='per_episode',command_mode='exec',environment={'COUNT':'${episodes}','PLACEHOLDER_SEED':'${seed}'})
    template['platform_bindings']=[{'source':'task.episodes','target':'argv','name':'--count'},
                                   {'source':'task.seed','target':'argv','name':'--mapped-seed'},
                                   {'source':'task.seed','target':'env','name':'MAPPED_SEED'}]
    monkeypatch.setattr('worker.host_backend.probe',lambda *a:None)
    host=HostBackend(w);host.create(task)
    launch=json.loads((host.root/'launch.json').read_text())
    if protocol==2:
        from runner.log_capture import capture
        assert capture(launch['command'],host.root,cwd=launch['cwd'],env=launch['environment'])==0
    else:
        result=subprocess.run(launch['command'],env=launch['environment'],cwd=launch['cwd'],capture_output=True,text=True,timeout=10)
        assert result.returncode==0,result.stderr
    sent=[]
    w.api=SimpleNamespace(post=lambda path,event:sent.append(event))
    w.save=lambda:None
    if protocol==2:
        while not Worker.send_protocol_events(w,task['execution_id'],host.root/'events.jsonl'):pass
    else:
        while not Worker.send_episode_journal(w,task['execution_id']):pass
    assert [(e['episode_id'],e['status']) for e in sent]==[(i,s) for i in range(1,11) for s in ('STARTED','COMPLETED')]
    assert all(e['exit_code']==0 for e in sent if e['status']=='COMPLETED')
    expected=[(initial_seed+i)%2147483648 for i in range(10)]
    assert [e['seed'] for e in sent if e['status']=='COMPLETED']==expected
    from worker.workspace import execution_dirs
    output=execution_dirs(w.workspace,task['execution_id'])/'output'
    observed=[json.loads((output/f'episode-{i}'/'observed.json').read_text()) for i in range(1,11)]
    assert observed==[{'episode':i+1,'seed':seed,'episodes':1} for i,seed in enumerate(expected)]


@pytest.mark.parametrize('protocol',[1,2])
def test_completed_episode_uploads_while_next_business_is_running(tmp_path,monkeypatch,protocol):
    import os
    import sys
    import time
    release=tmp_path/'release'
    marker=tmp_path/'second-started'
    (tmp_path/'business.py').write_text('''import json,os,time
from pathlib import Path
if os.environ['MINI_EVAL_EPISODE_ID']=='2':
    Path('second-started').touch()
    while not Path('release').exists():time.sleep(.01)
print('{"final":{"success_rate":1,"episode_count":1,"runtime":0.01}}',flush=True)
''')
    w=worker(tmp_path,['python3','business.py']);task=w.state['task'];task['episodes']=3
    spec=task['client_spec'];spec['service_mode']='none';template=spec['template']
    template.update(integration_mode='direct',episode_reporting=True)
    template['host_runtime'].update(episode_launch='per_episode',command_mode='exec')
    monkeypatch.setattr('worker.host_backend.probe',lambda *a:None)
    host=HostBackend(w);host.create(task)
    path=host.root/'launch.json';launch=json.loads(path.read_text())
    launch.update(resource_limits={},log_protocol=protocol);path.write_text(json.dumps(launch))
    process=subprocess.Popen([sys.executable,'-m','worker.host_supervisor',str(path)],env=os.environ.copy())
    sent=[];w.api=SimpleNamespace(post=lambda path,event:sent.append(event));w.save=lambda:None
    def upload():
        if protocol==2:Worker.send_protocol_events(w,task['execution_id'],host.root/'events.jsonl')
        else:Worker.send_episode_journal(w,task['execution_id'])
    try:
        end=time.monotonic()+10
        while time.monotonic()<end:
            upload()
            if marker.exists() and len(sent)>=3:break
            assert process.poll() is None
            time.sleep(.02)
        assert marker.exists() and process.poll() is None
        assert [(e['episode_id'],e['status']) for e in sent]==[(1,'STARTED'),(1,'COMPLETED'),(2,'STARTED')]
        release.touch();assert process.wait(timeout=10)==0
        upload()
        assert [(e['episode_id'],e['status']) for e in sent if e['status']=='COMPLETED']==[(1,'COMPLETED'),(2,'COMPLETED'),(3,'COMPLETED')]
    finally:
        release.touch()
        try:process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill();process.wait()
