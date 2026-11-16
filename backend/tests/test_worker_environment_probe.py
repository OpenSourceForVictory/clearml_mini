"""Probe configuration and asynchronous update behavior without touching system units."""
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from worker import host_environments as envs


@pytest.fixture
def profile(tmp_path):
    return {'python':sys.executable,'code_root':str(tmp_path)}


@pytest.mark.parametrize('field,value',[('python','relative'),('python','/missing/python'),('code_root','relative'),('code_root','/missing/code')])
def test_invalid_paths_fail_before_execution(monkeypatch,profile,field,value):
    profile[field]=value
    monkeypatch.setattr(envs.subprocess,'run',lambda *a,**kw:pytest.fail('invalid path executed'))
    with pytest.raises(ValueError,match='approved'):envs.probe(profile)


@pytest.mark.parametrize('kind',['cpu','npu'])
def test_approved_identity_used_for_python_probe(monkeypatch,profile,kind):
    from worker import host_identity,npu_host
    account=SimpleNamespace(pw_uid=1234,pw_gid=2345,pw_name='business')
    checks=[];runs=[];verified=[]
    monkeypatch.setattr(host_identity,'settings',lambda config:account)
    monkeypatch.setattr(npu_host,'settings',lambda config:(account,{}))
    monkeypatch.setattr(host_identity,'check_paths',lambda *a:checks.append(a))
    monkeypatch.setattr(envs.os,'getgrouplist',lambda *a:[2345,3456])
    monkeypatch.setattr(envs.subprocess,'run',lambda *a,**kw:runs.append((a,kw)))
    monkeypatch.setattr(host_identity,'verify_system',lambda a:verified.append(a))
    monkeypatch.setattr(npu_host,'probe',lambda config:verified.append(config))
    config={'host_execution':{'approved':True},'accelerator_type':kind}
    envs.probe(profile,config)
    assert checks[0][0] is account and len(checks[0][1])==2
    assert runs[0][1]['user']==1234 and runs[0][1]['extra_groups']==[2345,3456]
    assert runs[0][1]['cwd']=='/' and runs[0][1]['timeout']==5
    assert verified==[config if kind=='npu' else account]


def test_missing_user_manager_is_actionable(monkeypatch,profile):
    monkeypatch.setattr(envs.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=1))
    with pytest.raises(ValueError,match='enable linger'):envs.probe(profile)


def test_busy_update_is_deferred_and_persisted_when_idle(monkeypatch,tmp_path,profile):
    monkeypatch.setattr(envs,'probe',lambda *a:None)
    monkeypatch.setattr(envs,'verify_host',lambda:{'ready':True})
    state=envs.HostEnvironments(tmp_path/'worker')
    desired={'revision':2,'environments':{'business':profile}}
    state.update(desired,True);state.thread.join(2)
    assert state.report()['revision']==2 and state.report()['applied_revision']==0
    assert not state.path.exists()
    state.update(desired,False);state.thread.join(2)
    assert state.report()['environments']=={'business':{'ready':True,'error':None}}
    restored=envs.HostEnvironments(tmp_path/'worker')
    assert restored.record==desired
    state.probed_at-=31
    assert state.report()['environments']=={}


def test_probe_does_not_block_updates_and_discards_stale_results(monkeypatch,tmp_path,profile):
    entered=threading.Event();release=threading.Event()
    def slow(*args):entered.set();assert release.wait(3)
    monkeypatch.setattr(envs,'probe',slow)
    monkeypatch.setattr(envs,'verify_host',lambda:{'ready':True})
    state=envs.HostEnvironments(tmp_path/'worker')
    state.update({'revision':1,'environments':{'old':profile}},False)
    assert entered.wait(1)
    first_thread=state.thread
    state.update({'revision':2,'environments':{}},False)
    assert state.thread is first_thread and state.record['revision']==2
    release.set();first_thread.join(2)
    assert state.results=={} and state.probed_at==0
    state.update(state.record,False);state.thread.join(2)
    assert state.report()['applied_revision']==2


@pytest.mark.parametrize('resource_ready',[True,False])
def test_probe_failures_reported_without_crashing_heartbeat(monkeypatch,tmp_path,profile,resource_ready):
    monkeypatch.setattr(envs.os,'geteuid',lambda:1000)
    monkeypatch.setattr(envs,'verify_host',lambda:{'ready':resource_ready,'reason':'cpu controller missing'})
    def fail(*args):raise PermissionError('denied '*200)
    monkeypatch.setattr(envs,'probe',fail)
    state=envs.HostEnvironments(tmp_path/'worker')
    state.update({'revision':1,'environments':{'business':profile}},False);state.thread.join(2)
    report=state.report()['environments']['business']
    assert not report['ready']
    assert report['error']==('denied '*200)[:1000] if resource_ready else report['error']=='cpu controller missing'
    previous=state.thread
    state.update(state.record,False)
    assert state.thread is previous
