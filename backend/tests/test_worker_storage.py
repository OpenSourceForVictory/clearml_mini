import json
import subprocess
import uuid
from pathlib import Path
from types import SimpleNamespace
import pytest
from worker.client import Worker, docker
from worker.workspace import validate_storage, probe_storage
from worker.storage import Storage


def settings(tmp_path):
    for name in ('assets', 'cache'):
        (tmp_path/name).mkdir(mode=0o755)
    return dict(workspace_root=str(tmp_path/'work'), assets_root=str(tmp_path/'assets'), cache_root=str(tmp_path/'cache'), min_free_disk_gb=0)


def fake_docker(*args, **kwargs):
    return SimpleNamespace(returncode=0,stderr='')


def test_directory_validation_and_execution_separation(tmp_path, monkeypatch):
    cfg=settings(tmp_path)
    state=tmp_path/'private'/'state.json'
    validate_storage(cfg,state,create=True)
    (tmp_path/'link').symlink_to(tmp_path/'assets',target_is_directory=True)
    for path in ('/', '/proc', '/etc', '/run/a', str(tmp_path/'link'), str(tmp_path/'missing'), str(tmp_path)):
        with pytest.raises(ValueError):
            validate_storage({**cfg,'assets_root':path},state)
    for path in (str(tmp_path/'work'/'runs'),str(tmp_path/'private')):
        Path(path).mkdir(exist_ok=True)
        with pytest.raises(ValueError): validate_storage({**cfg,'assets_root':path},state)
    original=__import__('os').access
    monkeypatch.setattr('os.access',lambda p, mode: False if p==Path(cfg['workspace_root']) else original(p,mode))
    with pytest.raises(ValueError,match='writable'): validate_storage(cfg,state)


def test_switch_wait_restart_snapshot_and_bootstrap_restore(tmp_path):
    cfg=settings(tmp_path)
    original={**cfg,'workspace_root':str(tmp_path/'original'),'runner_image':'approved'}
    parent=Worker(original,tmp_path/'state.json');parent.slot_id='slot-a'
    execution=str(uuid.uuid4());child=parent.child(execution)
    child.state={'task':{'execution_id':execution},'storage':child.storage_snapshot};child.save()
    parent.storage.update({'revision':1,'config':cfg},parent.slot_id,True,fake_docker)
    assert parent.storage.report()['status']=='waiting'
    recovered=Worker(original,tmp_path/'state.json');recovered.recover_journals()
    assert recovered.children[execution].workspace==Path(original['workspace_root'])
    recovered.storage.update({'revision':1,'config':cfg},'slot-a',False,fake_docker)
    assert recovered.storage.snapshot()['config']['workspace_root']==cfg['workspace_root']+'/slots/slot-a'
    # Recovery reads execution snapshot even after a subsequent successful switch.
    again=Worker(original,tmp_path/'state.json');again.recover_journals()
    assert again.children[execution].workspace==Path(original['workspace_root'])
    assert again.path==tmp_path/'state.json'
    second=Storage(original,tmp_path/'other.json')
    second.update({'revision':1,'config':cfg},'slot-b',False,fake_docker)
    assert second.snapshot()['config']['workspace_root']!=again.storage.snapshot()['config']['workspace_root']
    again.storage.update({'revision':2,'config':None},'slot-a',False,fake_docker)
    assert again.storage.snapshot()['config']['workspace_root']==original['workspace_root']
    assert Path(cfg['workspace_root']).exists()


def test_probe_bounded_readonly_and_failure_retry(tmp_path):
    cfg=settings(tmp_path);validate_storage(cfg,tmp_path/'state',create=True)
    calls=[]
    def failed(*args,**kwargs):
        calls.append((args,kwargs))
        if args[0]=='run': raise subprocess.TimeoutExpired('docker',20)
        return fake_docker()
    store=Storage({**cfg,'runner_image':'approved'},tmp_path/'state')
    store.update({'revision':1,'config':cfg},'slot',False,failed)
    assert store.report()['status']=='failed' and store.snapshot()['revision']==0
    assert calls[0][1]['timeout']==20 and calls[1][0][:2]==('rm','-f')
    args=calls[0][0]
    assert args[args.index('--network')+1]=='none' and '--gpus' not in args and '--pull' in args
    assert sum(arg.endswith(',readonly') for arg in args)==2
    store.update({'revision':1,'config':cfg},'slot',False,failed)
    assert len(calls)==2
    store.update({'revision':2,'config':cfg},'slot',False,fake_docker)
    assert store.report()['status']=='applied' and store.snapshot()['revision']==2


def test_legacy_journal_gets_original_path_before_switch(tmp_path):
    cfg=settings(tmp_path)
    parent=Worker({**cfg,'runner_image':'approved'},tmp_path/'state')
    execution=str(uuid.uuid4())
    directory=Path(str(parent.path)+'.runs');directory.mkdir()
    path=directory/(execution+'.json');path.write_text(json.dumps({'task':{'execution_id':execution}}))
    parent.recover_journals()
    assert json.loads(path.read_text())['storage']['config']==parent.storage.record['bootstrap']
    assert parent.children[execution].storage_snapshot['revision']==0


def test_real_probe(tmp_path):
    import os
    image=os.getenv('STORAGE_PROBE_TEST_IMAGE')
    if not image: pytest.skip('set STORAGE_PROBE_TEST_IMAGE for real Docker probe')
    cfg=settings(tmp_path);validate_storage(cfg,tmp_path/'state',create=True)
    probe_storage(cfg,image,docker)
    with pytest.raises(RuntimeError,match='Docker command failed'):
        probe_storage(cfg,'mini-eval-storage-probe-does-not-exist:never',docker)


def test_shared_mount_cannot_expose_another_slot(tmp_path):
    cfg=settings(tmp_path)
    other=tmp_path/'work/slots/other/runs';other.mkdir(parents=True)
    with pytest.raises(ValueError,match='overlaps'):
        validate_storage({**cfg,'workspace_root':str(tmp_path/'work/slots/current'),
            'assets_root':str(other)},tmp_path/'state',create=True)
