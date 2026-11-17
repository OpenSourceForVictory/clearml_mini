"""Upgrade driver uses current release metadata and preserves operator files."""
import io
import json
import shutil
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from scripts import smoke_worker_upgrades as script
from worker.version import SOFTWARE_VERSION


@pytest.fixture
def upgrade(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('UPGRADE_TEST_PASSWORD','isolated-test')
    monkeypatch.setattr('worker.host_environments.user_environment',lambda:{})
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:tmp_path/'home'))
    data={'fault':None,'commands':[],'step':0,'requests':[]}
    def copy(source,dest,**kwargs):
        Path(dest).mkdir(parents=True)
        if source=='worker':(Path(dest)/'version.py').write_text('missing_version = True\n' if data['fault']=='source' else f'SOFTWARE_VERSION={SOFTWARE_VERSION!r}\n')
    monkeypatch.setattr(shutil,'copytree',copy)
    def run(argv,**kwargs):data['commands'].append(argv)
    monkeypatch.setattr(script.subprocess,'run',run)
    ticks=iter(range(1000));monkeypatch.setattr(script.time,'monotonic',lambda:next(ticks));monkeypatch.setattr(script.time,'sleep',lambda _:None)
    class Updater:
        def __init__(self,config):data['config']=config
        def step(self):
            data['step']+=1
            if data['step']==2 and data['fault']=='config':Path(data['config']['config_paths'][0]).write_text('{}')
            if data['step']==2 and data['fault']=='unit':
                (Path.home()/'.config/systemd/user'/data['config']['services'][0]['name']).write_text('changed')
    monkeypatch.setattr(script,'Updater',Updater)
    def request(req,timeout):
        assert urlsplit(req.full_url).netloc=='127.0.0.1:18093'
        path=urlsplit(req.full_url).path.removeprefix('/api/v1/');body=json.loads(req.data) if req.data else None
        data['requests'].append((path,body))
        if path=='auth/login':result={'access_token':'test-token'}
        elif path=='admin/worker-credentials':result={'id':'node','token':'node-token','node_name':body['node_name']}
        elif path=='admin/pools':result={'id':'pool'}
        elif path=='admin/worker-releases':result={'id':'new-digest'}
        elif path.endswith('/upgrades'):
            if body:result={'id':'operation'}
            else:
                after=data['step']==2
                source=next(tmp_path.glob('.runtime/worker-upgrades/*/app/worker/version.py'))
                scope={};exec(source.read_text(),scope)
                old=scope['SOFTWARE_VERSION']
                # Must genuinely differ from the current worker, without a stale version assumption.
                if not after:assert old!=SOFTWARE_VERSION,'upgrade fixture did not change current source version'
                result={'state':'FAILED' if data['fault']=='upgrade' else 'SUCCEEDED',
                        'slots':[{'id':str(i) if data['fault']!='identity' or not after else 'changed'+str(i),
                                  'online':data['fault']!='timeout','software':{'version':SOFTWARE_VERSION if after else old,
                                  'build_digest':'wrong' if data['fault']=='digest' else 'new-digest'}} for i in range(2)]}
        else:raise AssertionError(path)
        return io.BytesIO(json.dumps(result).encode())
    monkeypatch.setattr(script.urllib.request,'urlopen',request)
    return data


@pytest.mark.parametrize('fault',[None,'upgrade','identity','digest','config','unit','timeout'])
def test_upgrade_verdict_and_cleanup(upgrade,tmp_path,fault):
    upgrade['fault']=fault
    if fault=='timeout':
        with pytest.raises(RuntimeError,match='did not connect'):script.main()
    elif fault:
        with pytest.raises(AssertionError):script.main()
    else:script.main()
    evidence=list(tmp_path.rglob('evidence.json'))
    assert bool(evidence)==(fault is None)
    if evidence:
        result=json.loads(evidence[0].read_text())
        assert result['units_preserved'] and result['credentials_preserved']
        assert {s['software']['version'] for s in result['slots']}=={SOFTWARE_VERSION}
    assert not list((Path.home()/'.config/systemd/user').glob('*.service'))
    actions=[cmd[2] for cmd in upgrade['commands']]
    assert actions==['daemon-reload','start','start','stop','stop','daemon-reload']


def test_missing_version_assignment_fails_before_starting_units(upgrade):
    upgrade['fault']='source'
    with pytest.raises(RuntimeError,match='version fixture'):script.main()
    assert not upgrade['commands']
    assert not any(path=='admin/worker-credentials' for path,_ in upgrade['requests'])
