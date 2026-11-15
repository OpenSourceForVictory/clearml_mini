import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from contextlib import ExitStack
import fcntl
import pytest
from backend.tests.test_updater_executor import fixture
from backend.tests.test_execution import env
from backend.tests.test_worker_upgrades import setup
from backend.tests.test_enrollment import connect_agent
from backend.app.core import Slot,now
from worker.updater import Updater
from scripts import install_node_agent


def test_agent_install_preserves_worker_configuration_and_nfs_unit(tmp_path,monkeypatch):
    u,_,cfg,_,_=fixture(tmp_path);before=cfg.read_bytes()
    home=tmp_path/'home';home.mkdir()
    units=home/'.config/systemd/user';units.mkdir(parents=True)
    sentinel=units/'mini-eval-worker@test.service';sentinel.write_text('RequiresMountsFor=/mnt/eval-share')
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    layout=install_node_agent.install(u.config)
    assert cfg.read_bytes()==before and sentinel.read_text()=='RequiresMountsFor=/mnt/eval-share'
    assert (u.app/'node_agent/client.py').exists()
    value=json.loads(Path(layout['agent']['config_path']).read_text())
    assert value['token']=='private-test-token' and value['services']==u.config['services']
    assert Path(layout['agent']['config_path']).stat().st_mode&0o777==0o600
    unit=(units/'mini-eval-agent.service').read_text()
    assert 'node_agent.client' in unit and 'private-test-token' not in unit
    assert 'RequiresMountsFor' not in unit


def test_updater_controls_agent_before_workers_and_obeys_control_lock(tmp_path,monkeypatch):
    u,_,_,_,_=fixture(tmp_path);state=tmp_path/'agent';state.mkdir()
    u.config['agent']={'scope':'user','name':'mini-eval-agent.service','state_dir':str(state),'config_path':str(state/'config.json')}
    u=Updater(u.config);calls=[]
    monkeypatch.setattr('worker.updater.subprocess.run',lambda args,**kw:calls.append(args))
    u.control('stop');u.control('start')
    assert calls[0][-1]=='mini-eval-agent.service' and calls[-1][-1]=='mini-eval-agent.service'
    with (state/'control.lock').open('a') as held:
        fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            with ExitStack() as stack:u.idle_locks(stack,[0])


def test_agent_failure_rolls_back_entire_release(tmp_path):
    u,job,cfg,state,calls=fixture(tmp_path)
    (u.app/'node_agent').mkdir();(u.app/'node_agent/client.py').write_text('# old agent')
    # Missing/failed Agent reconnect is expressed as a failed verification receipt.
    u.api.fail_success=True
    u.apply(job)
    assert u.api.status=='FAILED' and (u.app/'node_agent/client.py').read_text()=='# old agent'
    assert calls==['stop','start','stop','start']


def test_upgrade_requires_actual_agent_digest(env):
    main,c,a=env;t,slots,identity,release=setup(env)
    connect_agent(c,t,'0'*64)
    r=c.post('/api/v1/admin/nodes/'+identity+'/upgrades',headers=a['admin'],json={'release_id':release['id'],'expected_revision':0})
    assert r.status_code==201,r.text
    job=c.post('/api/v1/worker/upgrades/poll',headers=t).json()['upgrade']
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0]);slot.software={'version':release['version'],'build_digest':release['id'],'capabilities':release['capabilities']};slot.protocol_version=release['protocol_version'];slot.last_seen_at=now();s.commit()
    path='/api/v1/worker/upgrades/'+job['id']+'/report'
    assert c.post(path,headers=t,json={'status':'SUCCEEDED'}).status_code==409
    connect_agent(c,t,release['id'])
    assert c.post(path,headers=t,json={'status':'SUCCEEDED'}).status_code==200


def test_agent_node_rejects_worker_only_archive_before_stopping(tmp_path):
    u,job,_,_,calls=fixture(tmp_path)
    agent_state=tmp_path/'agent';agent_state.mkdir()
    u.config['agent']={'name':'mini-eval-agent.service','scope':'user','state_dir':str(agent_state),'config_path':str(agent_state/'config.json')}
    u.apply(job)
    assert calls==[] and u.api.status=='FAILED'
    assert (u.app/'worker/client.py').read_text()=='# old worker'

@pytest.mark.parametrize('folder', ['ordinary', 'space and % percent'])
def test_agent_unit_passes_real_systemd_validation(tmp_path, monkeypatch, folder):
    import shutil
    import subprocess
    if not shutil.which('systemd-analyze'):
        pytest.skip('systemd-analyze unavailable')
    root=tmp_path/folder;root.mkdir()
    u,_,_,_,_=fixture(root)
    home=tmp_path/'home';home.mkdir()
    monkeypatch.setattr(Path,'home',classmethod(lambda cls:home))
    install_node_agent.install(u.config)
    unit=home/'.config/systemd/user/mini-eval-agent.service'
    result=subprocess.run(['systemd-analyze','verify',str(unit)],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
