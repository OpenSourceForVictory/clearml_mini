"""Safe deployment CLI tests; subprocess boundary never operates a real deployment."""
import hashlib
import stat
import sys
from types import SimpleNamespace

import pytest
from scripts import control


@pytest.mark.parametrize('docker,custom,plugin,legacy,expected',[
    (False,None,False,False,None),(True,'/custom/compose',False,False,['/custom/compose']),
    (True,None,True,False,['docker','compose']),(True,None,False,True,['docker-compose']),
    (True,None,False,False,None)])
def test_compose_discovery(monkeypatch,docker,custom,plugin,legacy,expected):
    monkeypatch.delenv('MINIEVAL_COMPOSE',raising=False)
    if custom:monkeypatch.setenv('MINIEVAL_COMPOSE',custom)
    monkeypatch.setattr(control.shutil,'which',lambda command:docker if command=='docker' else legacy)
    monkeypatch.setattr(control.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0 if plugin else 1))
    if expected:assert control.compose()==expected
    else:
        with pytest.raises(SystemExit):control.compose()


@pytest.fixture
def deployment(tmp_path,monkeypatch):
    env=tmp_path/'config.env';calls=[]
    monkeypatch.setattr(control,'compose',lambda:['docker','compose'])
    def run(argv,**kwargs):calls.append((argv,kwargs));return SimpleNamespace(returncode=0)
    monkeypatch.setattr(control.subprocess,'run',run)
    def invoke(*args):
        monkeypatch.setattr(sys,'argv',['control',*args,'--env-file',str(env)])
        control.main()
    return env,calls,invoke


def test_bootstrap_update_and_stop_preserve_data(deployment,capsys):
    env,calls,invoke=deployment
    invoke('up','--port','18000','--bind-host','::1')
    assert stat.S_IMODE(env.stat().st_mode)==0o600
    original=dict(line.split('=',1) for line in env.read_text().splitlines())
    assert original['API_BIND_HOST']=='[::1]' and original['API_BIND_PORT']=='18000'
    assert original['ADMIN_PASSWORD'] not in capsys.readouterr().out
    assert calls[0][0][-2:]==['build','api'] and calls[1][0][-3:]==['up','-d','--wait']
    invoke('up','--port','18001','--bind-host','127.0.0.1')
    updated=dict(line.split('=',1) for line in env.read_text().splitlines())
    assert updated['ADMIN_PASSWORD']==original['ADMIN_PASSWORD'] and updated['API_BIND_PORT']=='18001'
    invoke('status');assert calls[-1][0][-2:]==['ps','-a']
    invoke('down');assert calls[-1][0][-1]=='down' and '-v' not in calls[-1][0]
    assert env.exists()


@pytest.mark.parametrize('args',[('up','--port','0'),('up','--port','65536'),('status',),('down',)])
def test_invalid_or_uninitialized_never_deploys(deployment,args):
    env,calls,invoke=deployment
    with pytest.raises(SystemExit):invoke(*args)
    assert not calls and not env.exists()


@pytest.mark.parametrize('returncode',[0,1])
def test_backup_exclusive_private_and_failed_file_removed(deployment,monkeypatch,returncode):
    env,calls,invoke=deployment;env.write_text('EXISTING=yes\n');target=env.parent/'database.dump'
    def run(argv,**kwargs):
        assert argv[-6:]==['pg_dump','-U','eval','-d','eval','-Fc']
        kwargs['stdout'].write(b'database-bytes');return SimpleNamespace(returncode=returncode)
    monkeypatch.setattr(control.subprocess,'run',run)
    if returncode:
        with pytest.raises(SystemExit,match='incomplete file removed'):invoke('backup','--output',str(target))
        assert not target.exists() and not target.with_suffix('.dump.sha256').exists()
    else:
        invoke('backup','--output',str(target))
        assert stat.S_IMODE(target.stat().st_mode)==0o600
        assert target.with_suffix('.dump.sha256').read_text().startswith(hashlib.sha256(b'database-bytes').hexdigest())
        with pytest.raises(FileExistsError):invoke('backup','--output',str(target))
        assert target.read_bytes()==b'database-bytes'


def test_backup_requires_output(deployment):
    env,calls,invoke=deployment;env.write_text('EXISTING=yes\n')
    with pytest.raises(SystemExit):invoke('backup')
    assert not calls
