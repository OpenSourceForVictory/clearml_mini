import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
import zipfile
import pytest
from backend.tests.test_updater_executor import fixture
from scripts import install_node, deploy_node
from backend.app.worker_upgrades import build_release


def setup(tmp_path,monkeypatch):
    updater,_,credential,_,_=fixture(tmp_path)
    # fixture uses a fake API; installer verifies the actual configured identity.
    root=tmp_path/'bundle';root.mkdir();release=build_release()
    (root/'worker-release.zip').write_bytes(release.archive);(root/'release.sha256').write_text(release.id)
    value=json.loads(credential.read_text());new={**value,'token':'new-private'}
    monkeypatch.setattr(install_node,'ROOT',root)
    monkeypatch.setattr(install_node,'identity',lambda c:None)
    monkeypatch.setattr(install_node.API,'request',lambda *a,**kw:{'enrollment_state':'INSTALLING','enabled':False,'gpu_indices':[value.get('gpu_index',0)],'release_id':release.id})
    monkeypatch.setattr(install_node,'control_updater',lambda *a,**kw:None)
    monkeypatch.setattr(install_node.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=0))
    monkeypatch.setattr(install_node,'install_updater',lambda *a,**kw:None)
    monkeypatch.setattr(install_node.Updater,'control',lambda *a,**kw:None)
    updater.config['services']=[{'scope':'system','name':'mini-eval-worker-nfs.service'}]
    monkeypatch.setattr(install_node.os,'geteuid',lambda:0)
    return updater,credential,new,release


def test_complete_replace_preserves_local_settings_and_old_service(tmp_path,monkeypatch):
    u,path,new,r=setup(tmp_path,monkeypatch)
    old=json.loads(path.read_text());old['workspace_root']='/mnt/eval-share/work';old['assets']={'robotwin':'/mnt/asset'};path.write_text(json.dumps(old))
    services=list(u.config['services']);seen=[]
    monkeypatch.setattr(install_node,'install_updater',lambda layout,system:seen.append((layout,system)))
    install_node.replace_existing(u.config,new)
    assert json.loads(path.read_text())=={**old,'token':new['token']}
    assert seen[0][1] and seen[0][0]['services']==services
    assert (u.app/'worker/updater.py').exists() and (u.app/'runner/sdk.py').exists()
    assert json.loads((u.app/'.worker-release.json').read_text())['sha256']==r.id


@pytest.mark.parametrize('choice,expected',[('preserve',True),('disabled',False),('enabled',True)])
def test_reenrollment_changes_npu_sharing_only_when_selected(tmp_path,monkeypatch,choice,expected):
    u,path,new,_=setup(tmp_path,monkeypatch)
    original=json.loads(path.read_text())
    original.update(accelerator_type='npu',npu_runtime={'approved':True,'allow_shared':True,'host':{'approved':True,'user':'business'}})
    path.write_text(json.dumps(original))
    u.values[0]=original
    install_node.replace_existing(u.config,{**new,'accelerator_type':'npu','npu_sharing':choice})
    actual=json.loads(path.read_text())
    assert actual['npu_runtime']['allow_shared'] is expected
    assert actual['npu_runtime']['host']==original['npu_runtime']['host']
    assert actual['token']==new['token']


def test_reenrollment_rejects_unapproved_npu_sharing_before_stop(tmp_path,monkeypatch):
    u,path,new,_=setup(tmp_path,monkeypatch)
    old=path.read_bytes();actions=[]
    monkeypatch.setattr(install_node,'control_updater',lambda *a,**kw:actions.append(a))
    with pytest.raises(ValueError,match='approved NPU'):
        install_node.replace_existing(u.config,{**new,'accelerator_type':'npu','npu_sharing':'enabled'})
    assert not actions and path.read_bytes()==old


@pytest.mark.parametrize('choice,expected',[('preserve',None),('disabled',False),('enabled',True)])
def test_reenrollment_gpu_sharing_preserves_or_changes_local_policy(tmp_path,monkeypatch,choice,expected):
    u,path,new,_=setup(tmp_path,monkeypatch)
    original=json.loads(path.read_text())
    install_node.replace_existing(u.config,{**new,'accelerator_type':'gpu','gpu_sharing':choice})
    actual=json.loads(path.read_text())
    assert (actual.get('gpu_runtime') or {}).get('allow_shared') is expected
    assert actual['token']==new['token']
    assert actual.get('execution_modes')==original.get('execution_modes')


def test_install_failure_restores_code_and_credentials(tmp_path,monkeypatch):
    u,path,new,_=setup(tmp_path,monkeypatch);original=path.read_bytes();code=(u.app/'worker/client.py').read_bytes()
    monkeypatch.setattr(install_node,'install_updater',lambda *a:(_ for _ in ()).throw(ValueError('failed')))
    with pytest.raises(ValueError):install_node.replace_existing(u.config,new)
    assert json.loads(path.read_bytes())==json.loads(original)
    assert (u.app/'worker/client.py').read_bytes()==code


def test_failed_npu_sharing_change_restores_original_approval(tmp_path,monkeypatch):
    u,path,new,_=setup(tmp_path,monkeypatch)
    original=json.loads(path.read_text())
    original.update(accelerator_type='npu',npu_runtime={'approved':True,'allow_shared':True,'host':{'approved':True,'user':'business'}})
    path.write_text(json.dumps(original));u.values[0]=original
    monkeypatch.setattr(install_node,'install_updater',lambda *a:(_ for _ in ()).throw(RuntimeError('injected failure')))
    with pytest.raises(RuntimeError,match='injected failure'):
        install_node.replace_existing(u.config,{**new,'accelerator_type':'npu','npu_sharing':'disabled'})
    assert json.loads(path.read_text())==original


def test_local_journal_blocks_replacement(tmp_path,monkeypatch):
    u,path,new,_=setup(tmp_path,monkeypatch);original=path.read_bytes();code=(u.app/'worker/client.py').read_bytes()
    state=Path(json.loads(original)['state_path']);state.write_text('{"execution":"unfinished"}')
    with pytest.raises(ValueError,match='journal'):install_node.replace_existing(u.config,new)
    assert path.read_bytes()==original and (u.app/'worker/client.py').read_bytes()==code
    assert json.loads(state.read_text())['execution']=='unfinished'


def test_wrong_identity_and_missing_gpu_rejected_before_stopping(tmp_path,monkeypatch):
    u,_,new,_=setup(tmp_path,monkeypatch)
    with pytest.raises(ValueError,match='identity'):install_node.replace_existing(u.config,{**new,'node_name':'wrong'})
    monkeypatch.setattr(install_node.API,'request',lambda *a,**kw:{'enrollment_state':'INSTALLING','enabled':False,'gpu_indices':[999]})
    with pytest.raises(ValueError,match='GPU'):install_node.replace_existing(u.config,new)


def test_ssh_transfer_private_stdin_cleanup_and_host_validation(tmp_path,monkeypatch):
    from backend.app.enrollment import bundle
    data,_,_=bundle();archive=tmp_path/'node.zip';archive.write_bytes(data);secret=tmp_path/'credential.json';secret.write_text('{"token":"sensitive"}')
    calls=[]
    def run(argv,**kw):
        calls.append((argv,kw));return SimpleNamespace(stdout=b'/tmp/mini-eval-node.abcdefgh\n')
    monkeypatch.setattr(deploy_node.subprocess,'run',run)
    deploy_node.deploy(archive,secret,hashlib.sha256(data).hexdigest(),'user@host',installer_args=['--existing'])
    assert all('StrictHostKeyChecking=yes' in argv for argv,kw in calls)
    assert all('sensitive' not in str(argv) for argv,kw in calls)
    assert any(kw.get('input')==secret.read_bytes() for argv,kw in calls)
    assert calls[-1][0][-1].startswith('rm -rf -- /tmp/mini-eval-node.')
    with pytest.raises(ValueError):deploy_node.deploy(archive,secret,'0'*64,'user@host')
    with pytest.raises(ValueError):deploy_node.deploy(archive,secret,hashlib.sha256(data).hexdigest(),'-oProxyCommand=bad')


def test_bundle_traversal_rejected_before_ssh(tmp_path):
    out=io.BytesIO()
    with zipfile.ZipFile(out,'w') as z:z.writestr('../escape','x')
    data=out.getvalue()
    with pytest.raises(ValueError,match='member'):deploy_node.validate_bundle(data,hashlib.sha256(data).hexdigest())


def test_ssh_timeout_still_cleans_remote_private_directory(tmp_path,monkeypatch,capsys):
    import subprocess
    from backend.app.enrollment import bundle
    data,_,_=bundle();archive=tmp_path/'node.zip';archive.write_bytes(data);secret=tmp_path/'credential.json';secret.write_text('{"token":"sensitive"}')
    calls=[]
    def run(argv,**kw):
        calls.append(argv)
        if 'install_node.py' in argv[-1]: raise subprocess.TimeoutExpired(argv,300)
        return SimpleNamespace(stdout=b'/tmp/mini-eval-node.abcdefgh\n')
    monkeypatch.setattr(deploy_node.subprocess,'run',run)
    with pytest.raises(subprocess.TimeoutExpired):deploy_node.deploy(archive,secret,hashlib.sha256(data).hexdigest(),'user@host')
    assert calls[-1][-1].startswith('rm -rf -- /tmp/mini-eval-node.')
    assert 'sensitive' not in capsys.readouterr().out


def test_auxiliary_restore_preserves_bytes_permissions_and_disabled_services(tmp_path,monkeypatch):
    u,path,new,_=setup(tmp_path,monkeypatch)
    config=tmp_path/'agent.json';config.write_bytes(b'{"token":"old"}\n');config.chmod(0o600)
    unit=tmp_path/'agent.service';unit.write_bytes(b'original unit');unit.chmod(0o644)
    originals=[(p,p.read_bytes(),p.stat()) for p in (config,unit)]
    monkeypatch.setattr('scripts.node_layout.discover',lambda *a:{'checked':True})
    monkeypatch.setattr(install_node,'auxiliary_snapshot',lambda layout:originals)
    actions=[]
    monkeypatch.setattr(install_node.Updater,'control',lambda self,action:actions.append(action))
    def fail(*a):
        for p in (config,unit):p.write_bytes(b'changed');p.chmod(0o777)
        raise RuntimeError('auxiliary failure')
    monkeypatch.setattr(install_node,'install_updater',fail)
    with pytest.raises(RuntimeError,match='auxiliary failure'):
        install_node.replace_existing(u.config,new,checked_snapshot={'checked':True})
    for p,data,info in originals:
        assert p.read_bytes()==data and p.stat().st_mode==info.st_mode
    assert actions and set(actions)=={'stop'}


def test_stale_layout_rejected_before_services_stop(tmp_path,monkeypatch):
    u,_,new,_=setup(tmp_path,monkeypatch);calls=[]
    monkeypatch.setattr('scripts.node_layout.discover',lambda *a:{'changed':True})
    monkeypatch.setattr(install_node,'control_updater',lambda *a,**kw:calls.append(a))
    with pytest.raises(ValueError,match='environment_changed'):
        install_node.replace_existing(u.config,new,checked_snapshot={'checked':True})
    assert not calls


def test_auxiliary_snapshot_keeps_private_backup(tmp_path,monkeypatch):
    from scripts import node_layout
    updater=tmp_path/'updater';updater.mkdir();state=tmp_path/'state';state.mkdir()
    config=updater/'config.json';config.write_bytes(b'{"layout":true}')
    (updater/'updater.py').write_bytes(b'old updater')
    agent=tmp_path/'agent.json';agent.write_bytes(b'{"token":"private"}')
    units=tmp_path/'units';units.mkdir()
    for name in ('mini-eval-agent.service','mini-eval-updater.service'):(units/name).write_bytes(b'original')
    monkeypatch.setattr(node_layout,'LAYOUT',config)
    monkeypatch.setattr(node_layout,'trusted',lambda path:path)
    real=Path
    monkeypatch.setattr(install_node,'Path',lambda path:units/real(path).name if str(path).startswith('/etc/systemd/system/') else real(path))
    records=install_node.auxiliary_snapshot({'state_dir':str(state),'agent':{'config_path':str(agent)}})
    assert len(records)==5
    backups=list(state.glob('before-enrollment-*'));assert len(backups)==1
    assert backups[0].stat().st_mode & 0o777==0o700
    assert len(list(backups[0].iterdir()))==5
    assert all(p.stat().st_mode & 0o777==0o600 for p in backups[0].iterdir())
