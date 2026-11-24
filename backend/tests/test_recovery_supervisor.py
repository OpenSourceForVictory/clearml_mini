"""Durable local supervision, independent of HTTP and data delivery."""
import copy
import json
import threading
from types import SimpleNamespace

import pytest

from worker.recovery_supervisor import RecoverySupervisor, identity

IDENTITY={'kind':'host','boot_id':'boot','runtime_id':'a'*32}


def supervisor(path, **changes):
    return RecoverySupervisor(path, **{'execution_id':'e','generation':'g',
        'process_identity':IDENTITY,'tolerance':None,'started_at':0,'requested_at':0,**changes})


def api(action='track', duplicate=False):
    def post(path, body):
        return {'execution_id':'e','generation':'g','sequence':body['sequence'],
                'action':action,'reason':'cancelled' if action=='stop' else None,'duplicate':duplicate}
    return SimpleNamespace(post=post)


@pytest.mark.parametrize('tolerance,at,action',[(None,86400,'track'),(10,9.999,'track'),(10,10,'stop'),(10,100,'stop')])
def test_local_heartbeat_tolerance(tmp_path,tolerance,at,action):
    s=supervisor(tmp_path/'state',tolerance=tolerance)
    try:
        assert s.observe(IDENTITY,'running',at=at)==action
        assert s.snapshot()['state']=='running'
        assert s.snapshot()['stop_reason']==('disconnection_timeout' if action=='stop' else None)
    finally:s.close()


def test_restart_keeps_business_deadline_and_terminal_evidence(tmp_path):
    path=tmp_path/'state';s=supervisor(path,timeout_seconds=10)
    s.observe(IDENTITY,'running',at=9);s.close()
    s=supervisor(path,started_at=9,requested_at=9,timeout_seconds=10)
    try:
        assert s.observe(IDENTITY,'running',at=10)=='stop'
        assert s.snapshot()['stop_reason']=='execution_timeout'
        assert s.observe(IDENTITY,'exited',0,at=11)=='retain'
        assert s.observe(None,'missing',at=12)=='retain'
        assert s.snapshot()['state']=='exited' and s.snapshot()['exit_code']==0
        assert s.observe(IDENTITY,'running',at=13)=='inspect'
        assert s.snapshot()['exit_code']==0
    finally:s.close()


@pytest.mark.parametrize('change',[{'boot_id':'new'}, {'runtime_id':'b'*32}])
def test_reused_runtime_never_stopped(tmp_path,change):
    s=supervisor(tmp_path/'state',tolerance=1);stops=[]
    try:
        assert s.tick(lambda:{'identity':{**IDENTITY,**change},'state':'running'},stops.append,at=2)=='inspect'
        assert s.tick(lambda:{'identity':IDENTITY,'state':'running'},stops.append,at=3)=='inspect'
        assert not stops
        assert s.exchange(api(),clock=lambda:3) is False
    finally:s.close()


def test_lost_reply_replays_exact_body_after_restart(tmp_path):
    path=tmp_path/'state';s=supervisor(path);sent=[]
    def lost(path,body):
        sent.append(copy.deepcopy(body));raise ConnectionError('lost ACK')
    with pytest.raises(ConnectionError):s.exchange(SimpleNamespace(post=lost),clock=lambda:1)
    s.observe(IDENTITY,'exited',0,at=2);s.close()
    s=supervisor(path,started_at=10,requested_at=10)
    try:
        def replay(path,body):
            assert body==sent[0]
            return api(duplicate=True).post(path,body)
        assert s.exchange(SimpleNamespace(post=replay),clock=lambda:10)
        assert s.snapshot()['control']['clock']['confirmed_at']==0
        assert s.snapshot()['state']=='exited'
        def complete(path,body):
            assert body['state']=='exited' and body['exit_code']==0 and body['sequence']==2
            return api('retain').post(path,body)
        assert s.exchange(SimpleNamespace(post=complete),clock=lambda:11)
        assert s.snapshot()['control']['directive']=='retain'
    finally:s.close()


def test_http_block_cannot_block_local_timeout_or_reverse_it(tmp_path):
    s=supervisor(tmp_path/'state',tolerance=10)
    entered=threading.Event();release=threading.Event();errors=[]
    def blocked(path,body):
        entered.set();assert release.wait(5)
        return api().post(path,body)
    times=iter([1,11])
    def exchange():
        try:s.exchange(SimpleNamespace(post=blocked),clock=lambda:next(times))
        except BaseException as exc:errors.append(exc)
    thread=threading.Thread(target=exchange);thread.start()
    try:
        assert entered.wait(2)
        with pytest.raises(RuntimeError,match='already'):s.exchange(api())
        stopped=[]
        assert s.tick(lambda:{'identity':IDENTITY,'state':'running'},stopped.append,at=10)=='stop'
        assert stopped==[IDENTITY]
        release.set();thread.join(3)
        assert not thread.is_alive() and not errors
        assert s.snapshot()['control']['directive']=='stop'
    finally:release.set();thread.join(5);s.close()


def test_failed_stop_retried_without_claiming_cleanup(tmp_path):
    s=supervisor(tmp_path/'state',tolerance=1);stops=[]
    def stop(bound):
        stops.append(bound)
        if len(stops)==1:raise OSError('stop failed')
    try:
        with pytest.raises(OSError):s.tick(lambda:{'identity':IDENTITY,'state':'running'},stop,at=1)
        assert s.snapshot()['state']=='running'
        assert s.tick(lambda:{'identity':IDENTITY,'state':'running'},stop,at=2)=='stop'
        assert len(stops)==2 and s.snapshot()['state']=='running'
    finally:s.close()


@pytest.mark.parametrize('action,expected',[('stop','stop'),('inspect','inspect')])
def test_platform_directive_on_next_local_pass(tmp_path,action,expected):
    s=supervisor(tmp_path/'state')
    try:
        assert s.exchange(api(action),clock=lambda:1)
        assert s.observe(IDENTITY,'running',at=2)==expected
    finally:s.close()


def test_disk_failure_does_not_grant_control_or_change_facts(tmp_path,monkeypatch):
    import worker.recovery_supervisor as module
    s=supervisor(tmp_path/'state');before=s.snapshot()
    def broken(*args):raise OSError('disk full')
    monkeypatch.setattr(module,'atomic_json',broken)
    try:
        with pytest.raises(OSError):s.observe(IDENTITY,'exited',0,at=1)
        assert s.snapshot()==before
        with pytest.raises(OSError):s.exchange(api(),clock=lambda:1)
        assert s.snapshot()==before
    finally:s.close()


def test_single_owner_and_immutable_policy(tmp_path):
    path=tmp_path/'state';s=supervisor(path)
    try:
        with pytest.raises(BlockingIOError):supervisor(path)
    finally:s.close()
    for changes in ({'generation':'new'},{'tolerance':10},{'timeout_seconds':10},{'process_identity':{**IDENTITY,'boot_id':'new'}}):
        with pytest.raises(ValueError,match='changed'):supervisor(path,**changes)
    s=supervisor(path);s.close();s.close()


@pytest.mark.parametrize('value',[None,{}, {**IDENTITY,'runtime_id':'bad'},{**IDENTITY,'boot_id':''},{**IDENTITY,'kind':'docker'}])
def test_invalid_identity(value):
    with pytest.raises(ValueError):identity(value)


@pytest.mark.parametrize('args',[{'at':-1},{'at':float('inf')},{'at':True},{'state':'invalid','at':0},{'exit_code':0,'at':0},{'state':'exited','exit_code':True,'at':0}])
def test_bad_observation(tmp_path,args):
    s=supervisor(tmp_path/'state')
    try:
        with pytest.raises(ValueError):s.observe(IDENTITY,**{'state':'running',**args})
    finally:s.close()


def test_conflicting_exit_and_backwards_clock(tmp_path):
    s=supervisor(tmp_path/'state')
    try:
        s.observe(IDENTITY,'exited',0,at=5)
        with pytest.raises(ValueError):s.observe(IDENTITY,'exited',0,at=4)
        with pytest.raises(ValueError):s.exchange(api(),clock=lambda:4)
        assert s.observe(IDENTITY,'exited',1,at=6)=='inspect'
        assert s.snapshot()['exit_code']==0
    finally:s.close()


def test_journal_symlink_and_invalid_timeout(tmp_path):
    (tmp_path/'link').symlink_to(tmp_path/'state')
    with pytest.raises(ValueError,match='symlink'):supervisor(tmp_path/'link')
    with pytest.raises(ValueError,match='timeout'):supervisor(tmp_path/'state',timeout_seconds=-1)


def test_late_exit_does_not_invent_a_timeout(tmp_path):
    s=supervisor(tmp_path/'state',tolerance=1,timeout_seconds=1)
    try:
        assert s.observe(IDENTITY,'exited',0,at=100)=='retain'
        assert s.snapshot()['stop_reason'] is None
    finally:s.close()


def test_platform_completed_but_runtime_running_requires_inspection(tmp_path):
    s=supervisor(tmp_path/'state')
    try:
        s.exchange(api('retain'),clock=lambda:1)
        assert s.observe(IDENTITY,'running',at=2)=='inspect'
    finally:s.close()


def test_closed_supervisor_cannot_write_after_new_owner_takes_over(tmp_path):
    path=tmp_path/'state';old=supervisor(path);old.close()
    new=supervisor(path)
    try:
        new.observe(IDENTITY,'exited',0,at=1)
        with pytest.raises(RuntimeError,match='closed'):old.observe(IDENTITY,'running',at=2)
        with pytest.raises(RuntimeError,match='closed'):old.exchange(api(),clock=lambda:2)
        assert new.snapshot()['state']=='exited'
    finally:new.close()
