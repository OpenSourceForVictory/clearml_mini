import copy
import uuid

import pytest

from worker import recovery_launch as launch


def owner(path,**changes):
    return launch.RecoveryLaunch(path,**{'execution_id':'e','generation':'g',
        'request_id':'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','boot_id':'boot','kind':'host',**changes})


def reply(j,**changes):
    return {**j.record['assignment'],'launch_id':j.record['launch_id'], 'action':'launch',
            'reason':None,'duplicate':False,'valid_for_seconds':30,**changes}


def runtime(j,**changes):
    return {'launch_id':j.record['launch_id'],'identity':{'kind':'host','boot_id':'boot','runtime_id':'a'*32},**changes}


@pytest.fixture
def journal(tmp_path):
    j=owner(tmp_path/'launch')
    yield j
    j.close()


def test_confirm_start_restart_and_original_time(journal):
    j=journal;request=j.request(10);assert j.request(11)==request
    j.accept(reply(j),11)
    seen=[]
    def start(token):
        assert j.record['phase']=='attempted'
        seen.append(token);return runtime(j)
    record=j.start(12,start);assert record['started_at']==12
    assert j.bind(runtime(j))==record
    j.close();new=owner(j.path)
    try:
        assert new.record==record
        with pytest.raises(RuntimeError):new.start(100,start)
        with pytest.raises(RuntimeError):new.request(100)
        assert len(seen)==1
    finally:new.close()


def test_crash_after_start_before_identity_write_never_relaunches(journal):
    j=journal;j.request(1);j.accept(reply(j),2)
    count=[]
    def start(token):
        count.append(token);raise OSError('lost start response')
    with pytest.raises(OSError):j.start(3,start)
    j.close();new=owner(j.path)
    try:
        assert new.record['phase']=='attempted' and new.record['started_at']==3
        with pytest.raises(RuntimeError):new.start(4,start)
        assert len(count)==1
        new.bind(runtime(new))
    finally:new.close()


def test_disk_failure_before_attempt_does_not_call_launcher(journal,monkeypatch):
    j=journal;j.request(1);j.accept(reply(j),2)
    monkeypatch.setattr(launch,'atomic_json',lambda *a:(_ for _ in ()).throw(OSError('full')))
    with pytest.raises(OSError):j.start(3,lambda _:pytest.fail('must not start'))
    assert j.record['phase']=='prepared'


@pytest.mark.parametrize('phase',['accept','start'])
def test_expired_or_late_confirmation_blocks(journal,phase):
    j=journal;j.request(10)
    if phase=='accept':assert not j.accept(reply(j),40)
    else:
        j.accept(reply(j),11)
        with pytest.raises(RuntimeError):j.start(40,lambda _:pytest.fail('expired'))
    assert j.record['phase']=='blocked'
    with pytest.raises(ValueError):j.accept(reply(j),41)


@pytest.mark.parametrize('action',['stop','inspect'])
def test_stop_is_irreversible(journal,action):
    j=journal;j.request(1);assert not j.accept(reply(j,action=action),2)
    with pytest.raises(ValueError):j.accept(reply(j),3)
    with pytest.raises(RuntimeError):j.start(3,lambda _:pytest.fail('stopped'))


@pytest.mark.parametrize('change',[{'execution_id':'other'},{'generation':'other'},{'launch_id':str(uuid.uuid4())},
    {'boot_id':'other'},{'valid_for_seconds':0},{'valid_for_seconds':True},{'valid_for_seconds':31},
    {'duplicate':1},{'action':'track'},{'extra':1}])
def test_invalid_confirmation(journal,change):
    journal.request(1)
    with pytest.raises(ValueError):journal.accept(reply(journal,**change),2)
    assert journal.record['reply'] is None


def test_unconfirmed_and_backward_clock(journal):
    j=journal
    with pytest.raises(ValueError):j.accept(reply(j),1)
    with pytest.raises(RuntimeError):j.start(1,lambda _:None)
    with pytest.raises(RuntimeError):j.bind(runtime(j))
    j.request(10)
    with pytest.raises(ValueError):j.accept(reply(j),9)
    j.accept(reply(j),11)
    with pytest.raises(RuntimeError):j.start(9,lambda _:None)


@pytest.mark.parametrize('bad',[None,{'launch_id':'wrong'}, {'identity':None},
    {'identity':{'kind':'host','boot_id':'other','runtime_id':'a'*32}},
    {'identity':{'kind':'docker','boot_id':'boot','runtime_id':'a'*64}}])
def test_runtime_requires_launch_marker_boot_and_complete_identity(journal,bad):
    j=journal;j.request(1);j.accept(reply(j),2)
    observed=None if bad is None else runtime(j,**bad)
    with pytest.raises(ValueError):j.start(3,lambda _:observed)
    assert j.record['phase']=='attempted'
    j.bind(runtime(j))
    with pytest.raises(ValueError):j.bind(runtime(j,identity={'kind':'host','boot_id':'boot','runtime_id':'b'*32}))


def test_ownership_boot_changes_and_invalid_journal(journal,tmp_path):
    j=journal
    with pytest.raises(BlockingIOError):owner(j.path)
    j.close();j.close()
    with pytest.raises(RuntimeError):j.request(1)
    with pytest.raises(ValueError):owner(j.path,boot_id='different')
    link=tmp_path/'link';link.symlink_to(j.path)
    with pytest.raises(ValueError):owner(link)
    with pytest.raises(ValueError):owner(tmp_path/'invalid',kind='bad')


def test_identity_write_failure_preserves_attempt(journal,monkeypatch):
    j=journal;j.request(1);j.accept(reply(j),2)
    original=launch.atomic_json
    def start(token):
        monkeypatch.setattr(launch,'atomic_json',lambda *a:(_ for _ in ()).throw(OSError('disk full after spawn')))
        return runtime(j)
    with pytest.raises(OSError):j.start(3,start)
    assert j.record['phase']=='attempted'
    monkeypatch.setattr(launch,'atomic_json',original)
    j.bind(runtime(j))
    assert j.record['started_at']==3


def test_initial_disk_failure_and_request_disk_failure(tmp_path,monkeypatch):
    original=launch.atomic_json
    monkeypatch.setattr(launch,'atomic_json',lambda *a:(_ for _ in ()).throw(OSError('full')))
    with pytest.raises(OSError):owner(tmp_path/'j')
    monkeypatch.setattr(launch,'atomic_json',original)
    j=owner(tmp_path/'j')
    try:
        monkeypatch.setattr(launch,'atomic_json',lambda *a:(_ for _ in ()).throw(OSError('full')))
        with pytest.raises(OSError):j.request(1)
        assert j.record['requested_at'] is None
    finally:j.close()


@pytest.mark.parametrize('window',[float('nan'),float('inf'),-1,'30',None])
def test_launch_window_must_be_finite_bounded_number(journal,window):
    journal.request(1)
    with pytest.raises(ValueError):journal.accept(reply(journal,valid_for_seconds=window),2)
