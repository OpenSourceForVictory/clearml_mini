import json
import os
from pathlib import Path
from types import SimpleNamespace
import threading
import uuid
from datetime import datetime, timedelta, timezone
import pytest
from worker.retention import Retention, Protected, consume, tree, open_path


class Crash(BaseException):
    pass


@pytest.fixture
def host(tmp_path):
    root=tmp_path/'workspace';(root/'runs').mkdir(parents=True)
    state=tmp_path/'state'/'worker.json';state.parent.mkdir();state.write_text('null')
    worker=SimpleNamespace(path=state,storage_snapshot={'config':{'workspace_root':str(root)}},children={},threads={},start_lock=threading.Lock(),slot_id='slot')
    receipts=[]
    items={}
    def post(url,body=None):
        if url.endswith('/begin'):
            item=items[url.split('/')[-2]]
            return {'snapshot':item['snapshot'],'authorized_until':(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()}
        if url.endswith('/receipt'):receipts.append(body)
        return {}
    worker.api=SimpleNamespace(post=post)
    docker=lambda *args,**kwargs:SimpleNamespace(stdout='' if args[0]=='ps' else '[]')
    retention=Retention(worker,docker)
    def create():
        execution=str(uuid.uuid4());directory=root/'runs'/execution;directory.mkdir();(directory/'output').mkdir()
        (directory/'output'/'result').write_text('result')
        (directory/'work').mkdir();(directory/'work'/'input').write_text('input')
        entry=retention.scan_entry(next(iter(retention.roots)),execution)
        assert entry['scan_status']=='complete',entry
        snapshot={k:entry[k] for k in ('root_id','root_path','entry_name','generation','identity','logical_bytes','allocated_bytes')}
        snapshot['execution_id']=execution
        item={'id':str(uuid.uuid4()),'snapshot':snapshot};items[item['id']]=item
        return item,directory
    return retention,create,receipts,worker,docker


@pytest.mark.parametrize('phase',['intent','rename','staged','delete','removed','receipt'])
def test_crash_recovery_idempotent_no_replacement_delete(host,phase):
    r,create,receipts,w,docker=host
    item,directory=create()
    def fault(current):
        if current==phase:raise Crash()
    r.fault=fault
    with pytest.raises(Crash):r.execute(item)
    fresh=Retention(w,docker)
    fresh.execute(item)
    assert receipts[-1]['status']=='succeeded',receipts
    assert not directory.exists()
    directory.mkdir();(directory/'replacement').write_text('keep')
    fresh.execute(item)
    assert (directory/'replacement').read_text()=='keep'
    assert receipts[-1]==receipts[-2]


def test_sparse_hardlinks_symlinks_and_protected_assets(host,tmp_path):
    r,create,receipts,w,docker=host
    item,directory=create()
    target=tmp_path/'assets';target.mkdir();(target/'keep').write_text('asset')
    (directory/'link').symlink_to(target,target_is_directory=True)
    sparse=directory/'sparse'
    with sparse.open('wb') as f:f.truncate(10*1024*1024)
    os.link(sparse,directory/'hardlink')
    entry=r.scan_entry(item['snapshot']['root_id'],directory.name)
    assert entry['logical_bytes']>=10*1024*1024 and entry['logical_bytes']<11*1024*1024
    assert entry['allocated_bytes']<entry['logical_bytes']
    item['snapshot'].update({k:entry[k] for k in ('identity','logical_bytes','allocated_bytes','generation')})
    r.execute(item)
    assert receipts[-1]['status']=='succeeded'
    assert (target/'keep').read_text()=='asset'


def test_busy_journal_container_and_modified_tree(host):
    r,create,receipts,w,docker=host
    item,directory=create();execution=directory.name
    journals=Path(str(w.path)+'.runs');journals.mkdir()
    journal=journals/(execution+'.json');journal.write_text('{"task":{}}')
    assert r.scan_entry(item['snapshot']['root_id'],execution)['scan_status']=='blocked'
    r.execute(item);assert receipts[-1]['status']=='skipped';assert directory.exists()
    journal.unlink()
    item2=dict(item,id=str(uuid.uuid4()))
    r.docker=lambda *args,**kwargs:SimpleNamespace(stdout='container' if args[0]=='ps' else json.dumps([{'Mounts':[{'Source':str(directory)}]}]))
    r.execute(item2);assert receipts[-1]['status']=='skipped';assert directory.exists()
    r.docker=docker
    (directory/'new').write_text('changed')
    r.execute(dict(item,id=str(uuid.uuid4())))
    assert 'changed' in receipts[-1]['reason']


def test_unknown_roots_links_partial_failure_retry(host,tmp_path):
    r,create,receipts,w,docker=host
    assert r.scan_entry(next(iter(r.roots)),'probe-123')['scan_status']=='blocked'
    item,directory=create()
    def fail(phase):
        if phase=='delete':raise OSError('injected disk failure')
    r.fault=fail;r.execute(item)
    assert receipts[-1]['status']=='failed' and receipts[-1]['deleted_entries']==1
    entry=r.scan_entry(item['snapshot']['root_id'],directory.name,'staging')
    assert entry['scan_status']=='complete',entry
    newitem={'id':str(uuid.uuid4()),'snapshot':{**item['snapshot'],**{k:entry[k] for k in ('identity','generation','logical_bytes','allocated_bytes')}}}
    oldpost=w.api.post
    def post(path,body=None):
        if path.endswith('/begin'):return {'snapshot':newitem['snapshot'],'authorized_until':(datetime.now(timezone.utc)+timedelta(seconds=30)).isoformat()}
        return oldpost(path,body)
    w.api.post=post;r.fault=lambda phase:None;r.execute(newitem)
    assert receipts[-1]['status']=='succeeded',receipts
    assert not (directory.parent/'.cleanup'/directory.name).exists()


def test_root_replacement_and_source_replacement_after_intent(host,tmp_path):
    r,create,receipts,w,docker=host
    item,directory=create()
    def fault(phase):
        if phase=='intent':raise Crash()
    r.fault=fault
    with pytest.raises(Crash):r.execute(item)
    saved=directory.with_name('original');directory.rename(saved);directory.mkdir();(directory/'keep').write_text('keep')
    Retention(w,docker).execute(item)
    assert receipts[-1]['status']=='failed'
    assert (directory/'keep').read_text()=='keep' and saved.exists()
    root=directory.parent;root.rename(root.with_name('old-runs'));root.symlink_to(root.with_name('old-runs'),target_is_directory=True)
    r.execute(dict(item,id=str(uuid.uuid4())))
    assert receipts[-1]['status']=='skipped'
    assert (directory/'keep').read_text()=='keep'


def test_scan_resumes_in_bounded_steps_and_preserves_unknown(host):
    r,create,receipts,w,docker=host
    item,directory=create()
    for i in range(1200):(directory/str(i)).write_text('x')
    reports=[]
    w.api.post=lambda path,body=None:reports.extend(body['entries']) if path.endswith('/inventory') else {}
    scanner=r.scan()
    for _ in range(100):next(scanner)
    assert reports[0]['scan_status']=='partial' and len(reports)==1
    # Supervisor state is independent of scan progress, including an active task.
    w.children['unrelated']=object()
    for _ in scanner:pass
    assert reports[-1]['scan_status']=='complete'
    (directory.parent/'unknown-data').mkdir();(directory.parent/'unknown-data'/'keep').write_text('keep')
    assert r.scan_entry(item['snapshot']['root_id'],'unknown-data')['scan_status']=='blocked'
    assert (directory.parent/'unknown-data'/'keep').exists()


def test_cross_process_lock_and_offline_receipt(host):
    import fcntl,multiprocessing
    r,create,receipts,w,docker=host
    item,directory=create()
    locks=directory.parent/'.retention-locks';locks.mkdir(mode=0o700)
    ready=multiprocessing.Event();release=multiprocessing.Event()
    def holder():
        with (locks/directory.name).open('w') as f:
            fcntl.flock(f,fcntl.LOCK_EX);ready.set();release.wait(10)
    p=multiprocessing.get_context('fork').Process(target=holder);p.start();assert ready.wait(5)
    try:
        r.execute(item);assert receipts[-1]['status']=='skipped' and directory.exists()
    finally:release.set();p.join(5)
    # Lost receipt response is retried from disk; replacement files are not touched.
    item,directory=create();old=w.api.post
    def offline(path,body=None):
        if path.endswith('/receipt'):raise ConnectionError('offline')
        return old(path,body)
    w.api.post=offline
    with pytest.raises(ConnectionError):r.execute(item)
    assert not directory.exists()
    directory.mkdir();(directory/'replacement').write_text('keep')
    w.api.post=old;Retention(w,docker).execute(item)
    assert receipts[-1]['status']=='succeeded' and (directory/'replacement').exists()


def test_history_persists_old_root_and_permission_failure_is_not_zero(host,tmp_path,monkeypatch):
    r,create,receipts,w,docker=host
    item,directory=create()
    other=tmp_path/'new-workspace';(other/'runs').mkdir(parents=True)
    r.remember(other)
    w.storage_snapshot={'config':{'workspace_root':str(other)}}
    fresh=Retention(w,docker)
    assert len(fresh.roots)==2
    assert fresh.scan_entry(item['snapshot']['root_id'],directory.name)['scan_status']=='complete'
    import worker.retention as module
    original=module.child_dir
    def denied(parent,name,mount):
        if name=='output':raise PermissionError('permission denied')
        return original(parent,name,mount)
    monkeypatch.setattr(module,'child_dir',denied)
    value=fresh.scan_entry(item['snapshot']['root_id'],directory.name)
    assert value['scan_status']=='blocked' and 'logical_bytes' not in value and 'permission' in value['reason']
    assert (directory/'output'/'result').read_text()=='result'


def test_crash_after_final_rmdir_before_journal_commit(host,monkeypatch):
    r,create,receipts,w,docker=host
    item,directory=create();original=os.rmdir
    def crash(path,*args,**kwargs):
        result=original(path,*args,**kwargs)
        if path==directory.name:raise Crash()
        return result
    monkeypatch.setattr(os,'rmdir',crash)
    with pytest.raises(Crash):r.execute(item)
    monkeypatch.setattr(os,'rmdir',original)
    fresh=Retention(w,docker);fresh.execute(item)
    assert receipts[-1]['status']=='succeeded' and not directory.exists()
