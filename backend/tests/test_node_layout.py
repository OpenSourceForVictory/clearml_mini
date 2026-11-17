"""Custom system layouts use real files; systemd and ownership are simulated."""
import json
from pathlib import Path
from types import SimpleNamespace as N
import pytest
from scripts import node_layout as m


@pytest.fixture
def layout(tmp_path,monkeypatch):
    app=tmp_path/'app';(app/'worker').mkdir(parents=True);(app/'worker/client.py').write_text('pass')
    state=tmp_path/'state/updater';state.mkdir(parents=True)
    configs=tmp_path/'configs';configs.mkdir()
    agent=state.parent/'mini-eval-agent';agent.mkdir()
    updater=tmp_path/'updater';updater.mkdir();(updater/'updater.py').write_text('pass')
    paths=[];units=[];commands={}
    for i in range(8):
        p=configs/f'npu{i}.json';p.write_text(json.dumps(dict(node_name='node',api_url='http://test',token='secret',gpu_index=i,state_path=str(state.parent/f'npu{i}.json'))));paths.append(str(p))
        name=f'mini-eval-worker-npu{i}.service';units.append(dict(scope='system',name=name))
        commands[name]=(str(app),['/usr/bin/python3','-B','-m','worker.client','--config',str(p)])
    value=dict(app_dir=str(app),state_dir=str(state),config_paths=paths,services=units,agent=dict(scope='system',name='mini-eval-agent.service',state_dir=str(agent),config_path=str(agent/'config.json')))
    (agent/'config.json').write_text(json.dumps(dict(node_name='node',api_url='http://test',token='secret',services=units)))
    target=updater/'config.json';target.write_text(json.dumps(value))
    commands['mini-eval-agent.service']=(str(app),['/usr/bin/python3','-m','node_agent.client','--config',str(agent/'config.json')])
    commands['mini-eval-updater.service']=('', ['/usr/bin/python3',str(updater/'updater.py'),'--config',str(target)])
    monkeypatch.setattr(m,'LAYOUT',target);monkeypatch.setattr(m.os,'geteuid',lambda:0)
    # The root-only trust primitive has independent tests below.
    monkeypatch.setattr(m,'trusted',lambda p,directory=False:Path(p))
    def run(args,**kw):
        cwd,argv=commands[args[2]]
        return N(stdout='LoadState=loaded\nUser=root\nWorkingDirectory='+cwd+'\nExecStart={ path='+argv[0]+' ; argv[]='+' '.join(argv)+' ; ignore_errors=no ; }\n')
    monkeypatch.setattr(m.subprocess,'run',run)
    return value,commands,target


def test_eight_devices_and_configuration_digest(layout):
    value,_,target=layout
    first=m.discover('node','/usr/bin/python3')
    assert first['layout']==value and len(first['layout']['config_paths'])==8
    assert 'secret' not in json.dumps(first)
    p=Path(value['config_paths'][0]);v=json.loads(p.read_text());v['workspace_root']='/assets/original';p.write_text(json.dumps(v))
    assert m.discover('node','/usr/bin/python3')['fingerprint']!=first['fingerprint']


@pytest.mark.parametrize('case,error',[
 ('identity','identity_conflict'),('token','identity_conflict'),('duplicate-index','incomplete'),
 ('omitted-config','incomplete'),('duplicate-config','incomplete'),('missing-service','services_mismatch'),
 ('duplicate-service','services_mismatch'),('user-service','services_mismatch'),('wrong-cwd','services_mismatch'),
 ('wrong-python','services_mismatch'),('wrong-command','services_mismatch'),('duplicate-binding','services_mismatch'),
 ('agent-identity','identity_conflict'),('agent-path','services_mismatch'),('agent-command','services_mismatch'),
 ('state-in-app','untrusted'),('relative-state','untrusted'),('worker-journal','execution_journal_pending'),
 ('run-journal','execution_journal_pending'),('upgrade-journal','upgrade_journal_pending'),('empty','incomplete')])
def test_invalid_layout_is_rejected(layout,case,error):
    v,commands,target=layout;p=Path(v['config_paths'][0]);config=json.loads(p.read_text())
    if case=='identity':config['node_name']='other'
    if case=='token':config['token']='other'
    if case=='duplicate-index':config['gpu_index']=1
    if case=='omitted-config':v['config_paths'].pop()
    if case=='duplicate-config':v['config_paths'].append(v['config_paths'][0])
    if case=='missing-service':v['services'].pop()
    if case=='duplicate-service':v['services'][-1]=v['services'][0]
    if case=='user-service':v['services'][0]['scope']='user'
    if case=='wrong-cwd':commands[v['services'][0]['name']]=('/wrong',commands[v['services'][0]['name']][1])
    if case=='wrong-python':commands[v['services'][0]['name']][1][0]='/wrong/python'
    if case=='wrong-command':commands[v['services'][0]['name']][1][3]='another.module'
    if case=='duplicate-binding':commands[v['services'][1]['name']]=commands[v['services'][0]['name']]
    if case=='agent-identity':Path(v['agent']['config_path']).write_text('{}')
    if case=='agent-path':v['agent']['state_dir']='/wrong'
    if case=='agent-command':commands['mini-eval-agent.service'][1].append('--other')
    if case=='state-in-app':v['state_dir']=v['app_dir']
    if case=='relative-state':config['state_path']='relative'
    if case=='worker-journal':Path(config['state_path']).write_text('{"active":true}')
    if case=='run-journal':
        runs=Path(config['state_path']+'.runs');runs.mkdir();(runs/'pending.json').write_text('{"active":true}')
    if case=='upgrade-journal':(Path(v['state_dir'])/'upgrade.json').write_text('{}')
    if case=='empty':v['config_paths']=[]
    p.write_text(json.dumps(config));target.write_text(json.dumps(v))
    with pytest.raises(ValueError,match=error):m.discover('node','/usr/bin/python3')


def test_missing_layout_and_nonroot(layout,monkeypatch):
    _,_,target=layout;target.unlink();assert m.discover('node','/usr/bin/python3') is None
    monkeypatch.setattr(m.os,'geteuid',lambda:1000)
    with pytest.raises(ValueError,match='root'):m.discover('node','/usr/bin/python3')


@pytest.mark.parametrize('output',['LoadState=not-found\n','LoadState=loaded\nUser=other\n','LoadState=loaded\nUser=root\nExecStart=bad'])
def test_effective_service_must_be_loaded_root_and_parseable(monkeypatch,output):
    monkeypatch.setattr(m.subprocess,'run',lambda *a,**kw:N(stdout=output))
    with pytest.raises(ValueError,match='services_mismatch'):m.service('mini-eval-worker-x.service')


def test_trust_rejects_relative_symlink_and_writable_ancestors(tmp_path):
    with pytest.raises(ValueError,match='untrusted'):m.trusted(Path('relative'))
    p=tmp_path/'file';p.write_text('data');link=tmp_path/'link';link.symlink_to(p)
    with pytest.raises(ValueError,match='untrusted'):m.trusted(link)
    with pytest.raises(ValueError,match='untrusted'):m.trusted(p)
    assert m.trusted('/etc/passwd')==Path('/etc/passwd')
    assert m.trusted('/etc',True)==Path('/etc')
    with pytest.raises(ValueError,match='untrusted'):m.trusted('/etc')


def test_service_runtime_timestamps_do_not_invalidate_layout(layout,monkeypatch):
    first=m.discover('node','/usr/bin/python3')
    original=m.subprocess.run
    def changed_runtime(*a,**kw):
        result=original(*a,**kw)
        result.stdout=result.stdout.replace('ignore_errors=no ; }','ignore_errors=no ; start_time=[today] ; pid=123 ; }')
        return result
    monkeypatch.setattr(m.subprocess,'run',changed_runtime)
    assert m.discover('node','/usr/bin/python3')==first
