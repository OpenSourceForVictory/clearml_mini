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
