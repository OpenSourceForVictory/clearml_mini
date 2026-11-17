"""Isolated filesystem/SSH contracts; never run host systemctl or clean real nodes."""
import io
import json
from pathlib import Path
import pytest
from onboarding import cleanup,executor
from onboarding.contracts import Rejected
from backend.tests.test_ssh_onboarding import operations,form,wait


@pytest.fixture
def tree(tmp_path,monkeypatch):
    home=tmp_path/'control';units=tmp_path/'units';units.mkdir();home.mkdir()
    config=home/'.config/mini-eval';config.mkdir(parents=True)
    (config/'gpu.json').write_text(json.dumps({'node_name':'node'}))
    app=home/'.local/share/mini-eval-worker';app.mkdir(parents=True)
    (app/'program.py').write_text('pass\n')
    unit=units/'mini-eval-worker-managed-gpu.service'
    unit.write_text('[Service]\nUser=root\nWorkingDirectory='+str(app)+'\nExecStart=/usr/bin/python -m worker.client --config '+str(config/'gpu.json')+'\n')
    monkeypatch.setattr(cleanup,'HOMES',(str(home),))
    monkeypatch.setattr(cleanup,'UNITS',str(units))
    monkeypatch.setattr(cleanup,'LOCK',str(tmp_path/'install.lock'))
    monkeypatch.setattr(cleanup,'UPDATER',str(tmp_path/'updater'))
    monkeypatch.setattr(cleanup,'BACKUPS',str(tmp_path/'backups'))
    monkeypatch.setattr(cleanup.os,'geteuid',lambda:0)
    # /tmp is intentionally world writable; production trust is tested separately.
    monkeypatch.setattr(cleanup,'trusted',lambda p:p)
    commands=[]
    def control(*args):
        commands.append(args)
        if 'ActiveState' in args:return 'inactive\n'
        if args[0]=='show':return f'User=root\nFragmentPath={unit}\nDropInPaths=\n'
        return ''
    monkeypatch.setattr(cleanup,'systemctl',control)
    return home,unit,commands


def test_private_backup_manifest_preserves_business_and_state(tree):
    home,unit,commands=tree
    business=home/'business';business.mkdir();(business/'model').write_text('model')
    state=home/'.local/state/mini-eval';state.mkdir(parents=True);(state/'idle.json').write_text('{}')
    plan=cleanup.plan('node');result=cleanup.execute('node',plan,'a'*32)
    assert result['state']=='SUCCEEDED' and not unit.exists()
    backup=Path(result['backup']);assert backup.stat().st_mode&0o777==0o700
    manifest=json.loads((backup/'manifest.json').read_text())
    assert manifest['state']=='SUCCEEDED' and len(manifest['moved'])==3
    assert (business/'model').read_text()=='model' and (state/'idle.json').exists()
    assert any(cmd[0]=='disable' for cmd in commands) and commands[-1]==('daemon-reload',)
    assert cleanup.plan('node')['paths']==[]


@pytest.mark.parametrize('case',['wrong-node','journal','upgrade','user','custom','nonroot','drift','operation','service-user','drop-in','active','stop-failure','rename-failure','reload-failure'])
def test_cleanup_refuses_unsafe_or_keeps_partial_evidence(tree,monkeypatch,case):
    home,unit,commands=tree;plan=cleanup.plan('node');old=cleanup.systemctl
    if case=='wrong-node':(home/'.config/mini-eval/gpu.json').write_text('{"node_name":"other"}')
    if case in ('journal','upgrade'):
        p=home/('.local/state/mini-eval/task.json' if case=='journal' else '.local/state/mini-eval-updater/upgrade.json');p.parent.mkdir(parents=True);p.write_text('{"pending":true}')
    if case=='user':
        p=home/'.config/systemd/user/mini-eval-agent.service';p.parent.mkdir(parents=True);p.write_text('')
    if case=='custom':unit.write_text('WorkingDirectory=/outside\n')
    if case=='nonroot':monkeypatch.setattr(cleanup.os,'geteuid',lambda:1000)
    if case=='drift':(home/'.local/share/mini-eval-worker/program.py').write_text('changed')
    if case in ('service-user','drop-in'):
        monkeypatch.setattr(cleanup,'systemctl',lambda *args:old(*args).replace('User=root','User=alice') if case=='service-user' else old(*args).replace('DropInPaths=','DropInPaths=/override'))
    if case=='active':monkeypatch.setattr(cleanup,'systemctl',lambda *args:'active' if 'ActiveState' in args else old(*args))
    if case in ('stop-failure','reload-failure'):
        def fail(*args):
            if args[0]==('disable' if case=='stop-failure' else 'daemon-reload'):raise OSError('secret remote detail')
            return old(*args)
        monkeypatch.setattr(cleanup,'systemctl',fail)
    if case=='rename-failure':monkeypatch.setattr(cleanup.os,'rename',lambda *args:(_ for _ in ()).throw(OSError('failed')))
    if case in ('active','stop-failure','rename-failure','reload-failure'):
        result=cleanup.execute('node',plan,'a'*32)
        assert result['state']=='FAILED' and result['error']=='cleanup_partial_failure'
        assert json.loads((Path(result['backup'])/'manifest.json').read_text())['state']=='FAILED'
    else:
        with pytest.raises(ValueError):cleanup.execute('node',plan,'bad' if case=='operation' else 'a'*32)
        assert unit.exists() and not any(cmd[0]=='disable' for cmd in commands)


def test_trust_rejects_writable_parent_and_symlink(tmp_path):
    file=tmp_path/'file';file.write_text('data')
    with pytest.raises(ValueError,match='untrusted'):cleanup.trusted(file)
    link=tmp_path/'link';link.symlink_to(file)
    with pytest.raises(ValueError,match='untrusted'):cleanup.trusted(link)
    assert cleanup.trusted(Path('/'))==Path('/')


def test_remote_program_sanitizes_unexpected_errors(monkeypatch,capsys):
    monkeypatch.setattr(cleanup.sys,'stdin',io.StringIO('{"node_name":"node"}'))
    monkeypatch.setattr(cleanup,'plan',lambda _:(_ for _ in ()).throw(OSError('SECRET')))
    cleanup.main()
    assert json.loads(capsys.readouterr().out)=={'issues':['cleanup_inspection_failed']}


@pytest.mark.parametrize('case',['success','partial','drift','invalid','api-failure'])
def test_cleanup_executor_records_remote_receipt_before_api_callback(operations,monkeypatch,case):
    ops,calls=operations;original=executor.Control
    class Control(original):
        def call(self,path,body=None):
            if path.endswith('/cleanup/complete') and case=='api-failure':raise Rejected('control_api_rejected_401')
            return super().call(path,body)
    monkeypatch.setattr(executor,'Control',Control)
    plan={'paths':['/standard'],'services':[],'fingerprint':'a','issues':[]}
    def remote(r,f,p=None,i=None):
        if i:
            return {'issues':['cleanup_inspection_failed']} if case=='invalid' else {'state':'FAILED' if case=='partial' else 'SUCCEEDED','backup':'/root/backup'}
        return plan
    ops.cleanup_remote=remote
    job=ops.start('admin',form(fingerprint='SHA256:'+'A'*43,node_id='node'),'Bearer fixture','cleanup')
    identity=job['id'];wait(lambda:ops.jobs[identity]['state']=='READY')
    if case=='drift':ops.cleanup_remote=lambda *args:{**plan,'fingerprint':'b'}
    ops.confirm(identity,'admin','Bearer renewed')
    final=wait(lambda:ops.jobs[identity] if ops.jobs[identity]['state'] in ('SUCCEEDED','FAILED') else None)
    assert final['state']==('SUCCEEDED' if case=='success' else 'FAILED')
    if case=='api-failure':assert final['cleanup_result']['state']=='SUCCEEDED'
    if case=='drift':assert not any(p.endswith('/cleanup/begin') for p,_ in calls)
    assert not any('ssh-enrollment/begin' in p for p,_ in calls)
    assert identity not in ops.secrets


def test_orphan_system_unit_can_be_cleaned_after_installation_files_were_removed(tree):
    import shutil
    home,unit,commands=tree
    shutil.rmtree(home/'.config/mini-eval');shutil.rmtree(home/'.local/share/mini-eval-worker')
    # systemd User/WorkingDirectory can be empty for system services.
    unit.write_text('ExecStart=/usr/bin/python -m worker.client --config '+str(home/'.config/mini-eval/gpu.json')+'\nWorkingDirectory=\n')
    plan=cleanup.plan('node');assert plan['paths']==[str(unit)]
    assert cleanup.execute('node',plan,'a'*32)['state']=='SUCCEEDED'


@pytest.mark.parametrize('mode',['node-mismatch','custom-layout','mount','special','large','unsafe-exec'])
def test_extra_cleanup_path_guards(tree,monkeypatch,mode):
    home,unit,_=tree
    if mode in ('node-mismatch','custom-layout'):
        p=Path(cleanup.UPDATER);p.mkdir()
        (p/'config.json').write_text(json.dumps({'node_name':'other' if mode=='node-mismatch' else 'node','app_dir':'/custom'}))
    if mode=='mount':monkeypatch.setattr(cleanup.os.path,'ismount',lambda p:True)
    if mode=='special':
        import os
        os.mkfifo(home/'.config/mini-eval/pipe')
    if mode=='large':
        with (home/'.local/share/mini-eval-worker/large').open('wb') as f:f.truncate(33*1024*1024)
    if mode=='unsafe-exec':unit.write_text('ExecStart=/outside/script\n')
    with pytest.raises(ValueError):cleanup.plan('node')


def test_remote_cleanup_transport_is_bounded_and_validates_json(operations):
    ops,_=operations
    class Remote:
        def run(self,argv,data,**kwargs):
            assert argv[:2]==['python3','-c'] and kwargs['timeout']==180
            body=json.loads(data)
            assert body['node_name']=='ssh-test'
            return 0,json.dumps({'state':'SUCCEEDED'} if body.get('operation') else {'issues':[]}).encode(),b''
    assert ops.cleanup_remote(Remote(),form(python='python3'))=={'issues':[]}
    assert ops.cleanup_remote(Remote(),form(python='python3'),{},'a'*32)=={'state':'SUCCEEDED'}
    class Bad:
        def run(self,*a,**k):return 0,b'[]',b''
    with pytest.raises(Rejected):ops.cleanup_remote(Bad(),form(python='python3'))


@pytest.mark.parametrize('case',['outside-unit','stop-hook','prefix-trick'])
def test_cleanup_rejects_unreviewed_services(tree,monkeypatch,case):
    home,unit,_=tree;old=cleanup.systemctl
    if case=='outside-unit':monkeypatch.setattr(cleanup,'systemctl',lambda *args:'mini-eval-agent.service loaded active running Agent' if args[0]=='list-units' else old(*args))
    if case=='stop-hook':unit.write_text(unit.read_text()+'ExecStop=/custom/hook\n')
    if case=='prefix-trick':unit.write_text('ExecStart=/bin/anything '+str(home/'.config/mini-eval-unrelated')+'\n')
    with pytest.raises(ValueError,match='cleanup_service_unsupported'):cleanup.plan('node')


def test_cleanup_receipt_survives_retention_until_platform_acknowledges(operations):
    ops,_=operations
    job=ops.start('admin',form(),'Bearer fixture','cleanup');identity=job['id']
    wait(lambda:ops.jobs[identity]['state']=='NEEDS_HOST_KEY')
    with pytest.raises(Rejected):ops.acknowledge_cleanup(identity,'admin')
    ops.update(identity,'INTERRUPTED',error='executor_restarted_credentials_expired')
    ops.clock=lambda:job['created_at']+90000
    assert ops.list('admin')['operations'][0]['id']==identity
    with pytest.raises(Rejected):ops.acknowledge_cleanup(identity,'other')
    ops.acknowledge_cleanup(identity,'admin')
    ops.clock=lambda:job['created_at']+180000
    assert ops.list('admin')['operations']==[]


def test_invalid_config_shape_is_rejected_before_service_changes(tree):
    home,_,commands=tree
    (home/'.config/mini-eval/gpu.json').write_text('[]')
    with pytest.raises(ValueError,match='cleanup_invalid_configuration'):cleanup.plan('node')
    assert not any(x[0]=='disable' for x in commands)


@pytest.mark.parametrize('case',['custom-recovery','custom-updater-state','custom-updater-config','standard'])
def test_layout_cannot_hide_recovery_outside_reviewed_roots(tree,case):
    home,_,_=tree;updater=Path(cleanup.UPDATER);updater.mkdir()
    config=home/'.config/mini-eval/gpu.json'
    value={'app_dir':str(home/'.local/share/mini-eval-worker'),'state_dir':str(home/'.local/state/mini-eval-updater'),'config_paths':[str(config)]}
    if case=='custom-updater-state':value['state_dir']='/outside/updater'
    if case=='custom-updater-config':value['config_paths']=['/outside/worker.json']
    (updater/'config.json').write_text(json.dumps(value))
    config.write_text(json.dumps({'node_name':'node','state_path':'/outside/run.json' if case=='custom-recovery' else str(home/'.local/state/mini-eval/run.json')}))
    if case=='standard':assert not cleanup.plan('node')['issues']
    else:
        with pytest.raises(ValueError,match='cleanup_custom_layout_unsupported'):cleanup.plan('node')
