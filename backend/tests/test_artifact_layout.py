"""Independent paths and actual business argument/environment propagation."""
# ruff: noqa: F811
import json
import os
from pathlib import Path
import shutil
import sys
import uuid

import pytest
from worker import artifact_layout as layout
from runner.sdk import EpisodeResult, EvaluationClient, run
from backend.tests.test_worker_docker_contracts import container  # noqa: F401
from backend.tests.test_execution import env  # noqa: F401
from backend.tests.test_shared_artifacts import shared  # noqa: F401


def config(tmp):
    return {key:str(tmp/name) for key,name in [('workspace_root','work'),('assets_root','assets'),
        ('cache_root','cache'),('artifact_root','artifacts')]}


def task():
    return dict(artifact_layout='batch-v1',batch_id=str(uuid.uuid4()),job_id=str(uuid.uuid4()),execution_id=str(uuid.uuid4()))


@pytest.mark.parametrize('batch',[True,False])
def test_layout_retries_and_workspace_cleanup(tmp_path,batch):
    c=config(tmp_path);t=task()
    if not batch:t['batch_id']=None
    root=layout.prepare(c,t)
    assert root==Path(c['artifact_root'])/(t['batch_id'] or 'standalone')/t['job_id']/t['execution_id']
    (root/'output'/'result.json').write_text('{}')
    Path(c['workspace_root']).mkdir();shutil.rmtree(c['workspace_root'])
    assert (root/'output'/'result.json').read_text()=='{}'
    assert layout.prepare(c,{**t,'execution_id':str(uuid.uuid4())})!=root
    assert layout.prepare(c,t)==root
    assert layout.location(c,{'execution_id':t['execution_id']}) is None
    assert layout.output(c,{},tmp_path)==tmp_path/'output'


@pytest.mark.parametrize('invalid',['relative','/','/etc/eval','/tmp/../etc','same','inside','parent','symlink'])
def test_root_safety(tmp_path,invalid):
    c=config(tmp_path)
    if invalid=='same':c['artifact_root']=c['workspace_root']
    elif invalid=='inside':c['artifact_root']=c['assets_root']+'/artifacts'
    elif invalid=='parent':c['artifact_root']=str(tmp_path)
    elif invalid=='symlink':
        (tmp_path/'link').symlink_to('/tmp');c['artifact_root']=str(tmp_path/'link'/'artifacts')
    else:c['artifact_root']=invalid
    with pytest.raises(ValueError):layout.validate_root(c)


def test_identifiers_and_symlink_leaf(tmp_path):
    c=config(tmp_path);t=task()
    with pytest.raises(ValueError):layout.location(c,{**t,'job_id':'../escape'})
    with pytest.raises(ValueError):layout.location({**c,'artifact_root':None},t)
    root=layout.location(c,t);root.mkdir(parents=True)
    (root/'output').symlink_to(tmp_path)
    with pytest.raises(OSError):layout.prepare(c,t)


@pytest.mark.parametrize('runtime',[False,True])
def test_container_persistent_output_and_aliases(container,tmp_path,runtime):
    worker,t,calls,inputs=container
    worker.config['artifact_root']=str(tmp_path/'artifacts')
    t.update(task())
    if runtime:
        t['client_spec']['template']['runtime']={'output_dir':'/results','assets':{},
            'mounts':{'results':{'source':'output','target':'/business-results','read_only':False,'required':True}}}
    worker.create(t)
    root=layout.location(worker.config,t)
    mounts=[calls[0][i+1] for i,v in enumerate(calls[0]) if v=='--mount']
    assert f'type=bind,source={root},target=/tmp/mini-eval-artifacts' in mounts
    assert f'type=bind,source={root}/output,target=/tmp/mini-eval-output' in mounts
    if runtime:
        assert f'type=bind,source={root}/output,target=/results' in mounts
        assert f'type=bind,source={root}/output,target=/business-results' in mounts
    assert inputs[0]['_artifact_root']=='/tmp/mini-eval-artifacts'
    assert inputs[0]['_artifact_layout']=='batch-v1'


def test_host_output_and_working_directory(tmp_path,monkeypatch):
    from backend.tests.test_host_backend import worker
    from worker.host_backend import HostBackend
    (tmp_path/'app.py').write_text('pass')
    w=worker(tmp_path,['python3','app.py','--dest','${output_dir}'])
    w.config['artifact_root']=str(tmp_path/'artifacts')
    t=w.state['task'];t.update(task())
    host=HostBackend(w);host.system=False
    monkeypatch.setattr('worker.host_backend.probe',lambda *args:None)
    host.create(t)
    document=json.loads((w.workspace/'runs'/t['execution_id']/'input.json').read_text())
    output=layout.location(w.config,t)/'output'
    assert document['_resolved_command'][-1]==str(output)
    assert document['_resolved_environment']['MINI_EVAL_OUTPUT_DIR']==str(output)
    assert document['_resolved_cwd']==str(w.workspace/'runs'/t['execution_id']/'work')
    assert document['_artifact_root']==str(output.parent)


@pytest.mark.parametrize('nested',[False,True])
def test_sdk_context_paths_and_references(tmp_path,monkeypatch,nested):
    root=tmp_path/'attempt';common=root/'output';common.mkdir(parents=True)
    cfg={'episodes':1 if nested else 2,'seed':100,**layout.runner_config(root)}
    if nested:cfg['_parent_episode_id']=2;common=root/'episodes'/'2'/'output'
    monkeypatch.setenv('MINI_EVAL_OUTPUT_DIR',str(common))
    seen=[]
    class Client(EvaluationClient):
        def run_episode(self,c,e,ctx):
            target=ctx.episode_output_dir/'report.json';target.write_text(str(e.seed));seen.append(ctx)
            assert target.parent==(common if nested else root/'episodes'/str(e.id)/'output')
            return EpisodeResult(True,artifacts=[ctx.artifact_path(target)])
    run(Client(),cfg,common)
    assert seen[0].output_dir==common
    assert seen[0].artifact_path('common.json')==str((common/'common.json').relative_to(root))
    if not nested:assert seen[0].episode_output_dir!=seen[1].episode_output_dir


@pytest.mark.parametrize('ordinary',['ordinary','direct','sdk-command','sdk-command-whole'])
def test_actual_episode_process_receives_same_path_everywhere(tmp_path,monkeypatch,ordinary):
    from runner.host_episode import HostEpisodeClient
    root=tmp_path/'attempt';output=root/'output';output.mkdir(parents=True)
    work=tmp_path/'work';work.mkdir()
    monkeypatch.setenv('MINI_EVAL_OUTPUT_DIR',str(output));monkeypatch.setenv('MINI_EVAL_WORK_DIR',str(work))
    program=tmp_path/'business.py'
    program.write_text("""import json,os,sys
from pathlib import Path
document=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
dest=os.environ['MINI_EVAL_OUTPUT_DIR']
assert sys.argv[1]==dest
assert sys.argv[sys.argv.index('--output-dir')+1]==dest
assert os.environ['BUSINESS_OUTPUT']==dest
assert document['platform']['paths']['output']==dest
Path(dest,'result.json').write_text(json.dumps({'cwd':os.getcwd(),'seed':document['task']['seed']}))
if os.environ.get('MINI_EVAL_RESULT'):Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({'success':True,'metrics':{},'artifacts':['result.json']}))
print(json.dumps({'final':{'success_rate':1.0,'episode_count':1,'runtime':0.01}}))
""")
    cfg={'episodes':2,'seed':40,'task_name':'case','endpoint':'',**layout.runner_config(root),
         'template':{'execution_mode':'host','task_type':'command' if ordinary=='ordinary' else 'simulation',
           'host_runtime':{'episode_launch':'per_episode','environment':{}},
           'platform_bindings':[{'source':'platform.paths.output','target':'argv','name':'--output-dir','encoding':'literal'},
                                {'source':'platform.paths.output','target':'env','name':'BUSINESS_OUTPUT','encoding':'literal'}]}}
    command=[sys.executable,str(program),'${output_dir}']
    client=HostEpisodeClient(command,str(tmp_path),dict(os.environ),ordinary=='ordinary')
    if ordinary.startswith('sdk-command'):
        if ordinary=='sdk-command-whole':cfg['template']['host_runtime']['episode_launch']='whole_task'
        from runner.command_sdk import CommandClient
        cfg['template']['command']=command;cfg['_resolved_cwd']=str(tmp_path)
        client=CommandClient()
    run(client,cfg,output)
    for n in (1,2):
        value=json.loads((root/'episodes'/str(n)/'output'/'result.json').read_text())
        assert value=={'cwd':str(tmp_path),'seed':40 if ordinary=='sdk-command-whole' else 39+n}
    assert not (output/'result.json').exists()


def test_node_config_capability_gate(env):
    from backend.tests.test_storage import setup,save,report,CONFIG
    from backend.app.core import Slot,Worker,now
    from backend.app.storage import ready
    main,c,auth=env
    token,slots,path=setup(env,protocol=17)
    desired={**CONFIG,'artifact_root':'/shared/artifacts'}
    assert save(env,path,config=desired).status_code==200
    assert report(env,token,slots[0],config=desired).status_code==200
    assert c.get(path,headers=auth['admin']).json()['slots'][0]['status']=='upgrade_required'
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0]);worker=s.get(Worker,slot.worker_id)
        assert not ready(worker,slot,now())
        slot.software={'capabilities':['artifact_roots_v1']};s.commit()
        assert ready(worker,slot,now())


def test_independent_registration(shared):
    from backend.tests.test_shared_artifacts import route
    main,c,auth,token,job,t,output,mapping=shared
    root=Path(mapping['api_root'])/'standalone'/job/t['execution_id']
    (root/'output').mkdir(parents=True);(root/'output'/'data.json').write_text('{}')
    reply=c.post(f'/api/v1/worker/executions/{t["execution_id"]}/shared-artifacts',headers=token,
        json={'layout':'batch-v1','output_root':f'/node/share/standalone/{job}/{t["execution_id"]}'})
    assert reply.status_code==200,reply.text
    assert c.get(route(shared),headers=auth['alice']).json()['items'][0]['name']=='output'


def test_output_plan_frozen_across_claims_and_node_edits(shared):
    from backend.app.shared_artifacts import plan,save_root,snapshot
    from backend.app.core import Run,Slot,Worker,Job
    main,c,auth,token,jobid,t,output,mapping=shared
    with main.SessionLocal() as s:
        r=s.get(Run,t['run_id']);slot=s.get(Slot,r.slot_id);worker=s.get(Worker,slot.worker_id);job=s.get(Job,jobid)
        worker.storage_config={**config(Path('/node/share')),'artifact_root':'/node/share/artifacts'}
        slot.software={'capabilities':['artifact_roots_v1']}
        first=plan(s,r,worker,slot,job,True);s.commit()
        assert first['artifact_root']=='/node/share/artifacts'
        worker.storage_config={**worker.storage_config,'artifact_root':'/node/share/changed'};s.commit()
        assert plan(s,r,worker,slot,job,False)==first
        path=f'/node/share/artifacts/standalone/{jobid}/{t["execution_id"]}'
        with pytest.raises(Exception):save_root(s,r,worker.node_name,path+'-wrong','batch-v1')
        resolved=snapshot(s,r)
        assert resolved['output_root']==path and resolved['layout']=='batch-v1'
        assert plan(s,r,worker,slot,job,False)==first
        assert save_root(s,r,worker.node_name,path,'batch-v1')==resolved


def test_umask_and_plan_root_mismatch(tmp_path):
    c=config(tmp_path);t=task()
    original=os.umask(0o077)
    try:root=layout.prepare(c,t,container=True)
    finally:os.umask(original)
    assert root.stat().st_mode & 0o777==0o755
    assert (root/'episodes').stat().st_mode & 0o777==0o777
    with pytest.raises(ValueError,match='frozen'):
        layout.location(c,{**t,'artifact_root':str(tmp_path/'different')})


@pytest.mark.parametrize('failure',['success','denied','unprivileged'])
def test_business_account_creation_subprocess(tmp_path,monkeypatch,failure):
    from types import SimpleNamespace
    c=config(tmp_path);t=task();calls=[]
    account=SimpleNamespace(pw_uid=12345,pw_gid=12345,pw_name='business')
    monkeypatch.setattr(layout.os,'geteuid',lambda:0 if failure!='unprivileged' else 99)
    monkeypatch.setattr(layout.os,'getgrouplist',lambda *args:[12345])
    def invoke(command,**kw):
        calls.append((command,kw))
        return SimpleNamespace(returncode=int(failure=='denied'),stderr=b'Permission denied')
    monkeypatch.setattr(layout.subprocess,'run',invoke)
    if failure!='success':
        with pytest.raises(RuntimeError):layout.prepare(c,t,account)
    else:
        assert layout.prepare(c,t,account)==layout.location(c,t)
        assert calls[0][1]['user']==12345 and calls[0][1]['extra_groups']==[12345]
        assert calls[0][1]['cwd']=='/' and calls[0][0][1]=='-I'


@pytest.mark.skipif(os.environ.get('MINI_EVAL_TEST_ARTIFACT_DOCKER')!='1',reason='opt-in real independent artifact Docker test')
def test_real_docker_sdk_outputs_survive_deletion(tmp_path):
    import subprocess
    c=config(tmp_path);t=task();root=layout.prepare(c,t,container=True)
    repository=Path(__file__).resolve().parents[2]
    program="""import os,json
from pathlib import Path
from runner.sdk import EvaluationClient,EpisodeResult,run
assert os.getuid()==65534
class Business(EvaluationClient):
 def run_episode(self,task,episode,context):
  p=context.episode_output_dir/'result.json'
  p.write_text(json.dumps({'seed':episode.seed,'uid':os.getuid()}))
  return EpisodeResult(True,artifacts=[context.artifact_path(p)])
run(Business(),{'episodes':2,'seed':60,'_artifact_layout':'batch-v1','_artifact_root':'/results'},'/results/output')
"""
    name='mini-eval-artifact-layout-'+t['execution_id']
    try:
        completed=subprocess.run(['docker','run','--rm','--name',name,'--network','none','--user','65534:65534','--workdir','/opt/app',
            '--mount',f'type=bind,source={root},target=/results',
            '--mount',f'type=bind,source={repository}/runner,target=/opt/app/runner,readonly',
            '-e','PYTHONPATH=/opt/app','--entrypoint','python','mini-eval-runner:dev','-c',program],
            check=False,capture_output=True,text=True,timeout=40)
        assert completed.returncode==0,completed.stderr
        assert subprocess.run(['docker','inspect',name],capture_output=True).returncode!=0
        for n in (1,2):
            assert json.loads((root/'episodes'/str(n)/'output'/'result.json').read_text())=={'seed':59+n,'uid':65534}
    finally:
        # Only files created under this test's fresh execution root are reclaimed.
        subprocess.run(['docker','run','--rm','--network','none','--user','0:0','--mount',
            f'type=bind,source={root},target=/results','--entrypoint','python','mini-eval-runner:dev','-c',
            'import os;from pathlib import Path;[(os.chown(p,'+str(os.getuid())+','+str(os.getgid())+')) for p in Path("/results").rglob("*")]'],
            check=True,capture_output=True,timeout=30)


def test_independent_container_reserved_mount_conflict(container,tmp_path):
    worker,t,calls,inputs=container;worker.config['artifact_root']=str(tmp_path/'artifacts');t.update(task())
    t['client_spec']['template']['runtime']={'output_dir':'/tmp/mini-eval-artifacts/conflict','assets':{}}
    with pytest.raises(RuntimeError,match='overlaps independent'):worker.create(t)
    assert not calls


def test_approved_creation_account(tmp_path,monkeypatch):
    import pwd
    c=config(tmp_path);c['host_execution']={'approved':True}
    account=pwd.getpwuid(os.geteuid())
    monkeypatch.setattr(layout.os,'geteuid',lambda:0)
    monkeypatch.setattr('worker.host_identity.settings',lambda _:account)
    seen=[]
    from types import SimpleNamespace
    monkeypatch.setattr(layout.subprocess,'run',lambda *a,**kw:(seen.append(kw) or SimpleNamespace(returncode=0)))
    assert layout.prepare(c,task())
    if account.pw_uid:assert seen[0]['user']==account.pw_uid
