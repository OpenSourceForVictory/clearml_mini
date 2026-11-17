"""Read-only remote probe contracts with synthetic OS/driver responses."""
from pathlib import Path
from types import SimpleNamespace as N
import pytest
from onboarding import probe

@pytest.fixture
def environment(tmp_path,monkeypatch):
    home=tmp_path/'home';home.mkdir();units=tmp_path/'units';units.mkdir()
    original=Path
    def paths(value):
        if str(value).startswith('/etc/systemd/system'):return units/str(value).removeprefix('/etc/systemd/system').lstrip('/')
        if value=='/var/lib/mini-eval/control':return tmp_path/'managed'
        return original(value)
    class Paths:
        def __new__(cls,value):return paths(value)
        @staticmethod
        def home():return home
    monkeypatch.setattr(probe,'Path',Paths)
    monkeypatch.setattr(probe.os,'geteuid',lambda:0);monkeypatch.setattr(probe.os,'getuid',lambda:0)
    monkeypatch.setattr(probe.shutil,'which',lambda _: '/usr/bin/systemctl')
    class Response:
        status=200
        def __enter__(self):return self
        def __exit__(self,*a):pass
    monkeypatch.setattr(probe.urllib.request,'build_opener',lambda *a:N(open=lambda *a,**kw:Response()))
    calls=[]
    def run(argv,**kw):
        calls.append(argv)
        return N(returncode=0,stdout='0, RTX, GPU-A\n1, RTX, GPU-B\n' if argv[0]=='nvidia-smi' else '')
    monkeypatch.setattr(probe.subprocess,'run',run)
    options={'node_name':'fresh','mode':'host','gpus':'all','api_url':'http://test'}
    return options,calls,home,units


def test_root_gpu_does_not_need_user_session(environment):
    options,calls,*_=environment;result=probe.collect(options)
    assert not result['issues'] and result['selected_gpus']==[0,1]
    assert result['business_user']=='mini-eval' and result['business_uid']==29000
    assert not any('--user' in c for c in calls)


def test_new_nonroot_is_actionable(environment,monkeypatch):
    options,*_=environment;monkeypatch.setattr(probe.os,'geteuid',lambda:1000)
    assert 'new_node_requires_root' in probe.collect(options)['issues']


def test_existing_identity_not_replaced(environment):
    options,_,_,units=environment;(units/'mini-eval-agent.service').write_text('existing')
    assert 'existing_installation_requires_identity_migration' in probe.collect(options)['issues']


def test_conflicting_uid_stops_probe(environment,monkeypatch):
    import pwd
    options,*_=environment
    monkeypatch.setattr(pwd,'getpwuid',lambda _:N(pw_name='other'))
    assert 'business_identity_conflict' in probe.collect(options)['issues']

@pytest.mark.parametrize('valid',[True,False])
def test_npu_uses_ascend_parser_and_health(environment,monkeypatch,valid):
    options,calls,*_=environment
    options.update(accelerator_type='npu',ascend_source=Path('node_agent/ascend.py').read_text())
    mapping='0 0 0 Ascend 910B\n'
    info='HBM-Usage(MB) AICore(%)\n| 0 910B | OK | 0 |\n| 0 | 0000:01:00.0 | 0 0/100 0/65536 |\n'
    def run(argv,**kw):
        calls.append(argv)
        return N(returncode=0 if valid else 1,stdout=mapping if '-m' in argv else info)
    monkeypatch.setattr(probe.subprocess,'run',run)
    result=probe.collect(options)
    assert not any(c[0]=='nvidia-smi' for c in calls)
    if valid:assert result['gpus']==[{'index':0,'name':'Ascend 910B','uuid':'ascend:0000:01:00.0:0'}] and not result['issues']
    else:assert 'npu_driver_unavailable' in result['issues']


def test_docker_v1_missing_runner_is_preparation_not_rejection(environment,monkeypatch):
    options,calls,*_=environment;options.update(mode='docker',runner_image='mini-eval-runner:dev')
    original=Path.exists
    monkeypatch.setattr(Path,'exists',lambda p:False if str(p)=='/sys/fs/cgroup/cgroup.controllers' else original(p))
    def run(args,**kwargs):
        calls.append(args)
        return N(returncode=1 if args[:3]==['docker','image','inspect'] else 0,stdout='0, RTX, GPU-A\n' if args[0]=='nvidia-smi' else '')
    monkeypatch.setattr(probe.subprocess,'run',run)
    result=probe.collect(options)
    assert result['issues']==[] and result['runner_image_ready'] is False
    assert result['cgroup_mode']=='v1-or-hybrid'
    assert not any('pull' in c or 'build' in c for c in calls)


def test_both_execution_modes_probe_docker_and_expose_gpu_policy(environment):
    options,calls,*_=environment
    result=probe.collect({**options,'mode':'both','runner_image':'runner'})
    assert result['issues']==[]
    assert ['docker','info'] in calls
    assert ['docker','image','inspect','runner'] in calls


def test_custom_layout_probe_preserves_business_user_and_services(environment):
    import json
    options,_,home,_=environment
    p=home/'legacy.json';p.write_text(json.dumps(dict(node_name='fresh',state_path=str(home/'state.json'),gpu_index=0,npu_runtime={'host':{'user':'original-business'}})))
    installation={'layout':{'config_paths':[str(p)],'services':[{'scope':'system','name':'mini-eval-worker-legacy.service'}]},'fingerprint':'hash'}
    options.update(gpus='0',node_id='original',layout_source='def discover(*a): return '+repr(installation))
    result=probe.collect(options)
    assert result['issues']==[] and result['existing']
    assert result['business_user']=='original-business' and result['installation']==installation
    assert result['services']==installation['layout']['services']


def test_existing_npu_probe_shows_sharing_without_full_config(environment,monkeypatch):
    import json
    options,_,home,_=environment
    path=home/'npu.json'
    path.write_text(json.dumps({'node_name':'fresh','gpu_index':0,'accelerator_type':'npu',
        'state_path':str(home/'state.json'),'token':'PRIVATE',
        'npu_runtime':{'approved':True,'allow_shared':True,'host':{'user':'business'}}}))
    installation={'layout':{'config_paths':[str(path)],'services':[{'scope':'system','name':'worker.service'}]},'fingerprint':'hash'}
    options.update(gpus='0',node_id='original',accelerator_type='npu',runner_image='runner',
        ascend_source=Path('node_agent/ascend.py').read_text(),layout_source='def discover(*a): return '+repr(installation))
    mapping='0 0 0 Ascend 910B\n'
    info='HBM-Usage(MB) AICore(%)\n| 0 910B | OK | 0 |\n| 0 | 0000:01:00.0 | 0 0/100 0/65536 |\n'
    monkeypatch.setattr(probe.subprocess,'run',lambda argv,**kw:N(returncode=0,stdout=mapping if '-m' in argv else info))
    result=probe.collect(options)
    assert result['configs'][0]['npu_sharing'] is True
    assert 'PRIVATE' not in json.dumps(result)


@pytest.mark.parametrize('error',['existing_layout_untrusted','upgrade_journal_pending','private secret'])
def test_custom_layout_failure_is_safe_and_blocks(environment,error):
    options,*_=environment;options['layout_source']='def discover(*a): raise ValueError('+repr(error)+')'
    result=probe.collect(options)
    assert result['installation'] is None
    assert result['issues']==[error if error!='private secret' else 'existing_layout_invalid']
