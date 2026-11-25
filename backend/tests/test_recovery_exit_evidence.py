import copy
import json
from pathlib import Path
from types import SimpleNamespace
import uuid

import pytest

from worker.recovery_runtime import RecoveryRuntime
from worker.host_backend import HostBackend


@pytest.fixture
def case(tmp_path,monkeypatch):
    execution=str(uuid.uuid4());launch=str(uuid.uuid4())
    proof={'generation':'g','identity':{'kind':'host','boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),'runtime_id':'a'*32}}
    w=SimpleNamespace(path=tmp_path/'state',state={'task':{'execution_id':execution,'recovery_launch_id':launch},'recovery':proof},host_mode=lambda:True)
    raw=[{'State':{'Running':True,'Status':'running'},'InvocationID':'a'*32,'LaunchID':launch}]
    monkeypatch.setattr(HostBackend,'inspect',lambda *a,**k:copy.deepcopy(raw[0]))
    # Avoid constructing a real backend: the runtime adapter owns these calls.
    monkeypatch.setattr(RecoveryRuntime,'_raw',lambda self:copy.deepcopy(raw[0]))
    monkeypatch.setattr(HostBackend,'__init__',lambda self,worker:None)
    calls=[]
    def stop(self,e):calls.append(e);raw[0]=None
    monkeypatch.setattr(HostBackend,'stop',stop)
    return w,RecoveryRuntime(w,execution),raw,calls


def test_confirmed_stop_retains_identity_and_unknown_exit_across_restart(case,monkeypatch):
    w,r,raw,calls=case;bound=w.state['recovery']['identity']
    r.stop(bound)
    result=r.inspect()
    assert result=={'state':'exited','identity':bound,'exit_code':None,'retained':True}
    assert RecoveryRuntime(w,r.execution_id).inspect()==result
    assert r.inspect_launch(w.state['task']['recovery_launch_id'])['identity']==bound
    monkeypatch.setattr(HostBackend,'remove',lambda *a:pytest.fail('already absent'))
    r.remove(bound);r.stop(bound);assert len(calls)==1
    with pytest.raises(RuntimeError):r.remove({**bound,'runtime_id':'b'*32})
    with pytest.raises(ValueError):r.inspect_launch(str(uuid.uuid4()))


def test_absence_without_confirmed_stop_remains_unknown(case):
    w,r,raw,calls=case;raw[0]=None
    assert r.inspect()=={'state':'missing','identity':None,'exit_code':None}
    r.stop(w.state['recovery']['identity'])
    assert not r._exit_path().exists() and calls==[]


@pytest.mark.parametrize('fault',['running','identity','exception','disk'])
def test_failed_or_unconfirmed_stop_cannot_create_exit_evidence(case,monkeypatch,fault):
    w,r,raw,calls=case
    def stop(self,e):
        if fault=='exception':raise TimeoutError('stop timed out')
        if fault=='identity':raw[0]['InvocationID']='b'*32;raw[0]['State']['Running']=False
        if fault=='disk':raw[0]=None
    monkeypatch.setattr(HostBackend,'stop',stop)
    if fault=='disk':monkeypatch.setattr('worker.recovery_runtime.atomic_json',lambda *a:(_ for _ in ()).throw(OSError('disk full')))
    with pytest.raises((RuntimeError,OSError)):r.stop(w.state['recovery']['identity'])
    assert not r._exit_path().exists()


@pytest.mark.parametrize('field',['generation','execution_id','launch_id','boot','code','state','identity','symlink','oversize'])
def test_foreign_or_corrupted_exit_evidence_is_rejected(case,field):
    w,r,raw,_=case;r.stop(w.state['recovery']['identity']);p=r._exit_path();saved=json.loads(p.read_text())
    if field in ('generation','execution_id','launch_id'):saved[field]='foreign'
    elif field=='boot':saved['observation']['identity']['boot_id']='previous-boot'
    elif field=='identity':saved['observation']['identity']['runtime_id']='b'*32
    elif field=='code':saved['observation']['exit_code']=True
    elif field=='state':saved['observation']['state']='running'
    elif field=='symlink':
        target=p.with_suffix('.saved');p.rename(target);p.symlink_to(target)
    elif field=='oversize':p.write_text(' '*4097)
    if field not in ('symlink','oversize'):p.write_text(json.dumps(saved))
    with pytest.raises(ValueError):r.inspect()


def test_natural_exit_is_persisted_once_and_never_overwritten(case,monkeypatch):
    w,r,raw,_=case;raw[0]['State'].update(Running=False,ExitCode=7)
    assert r.inspect()['exit_code']==7
    monkeypatch.setattr('worker.recovery_runtime.atomic_json',lambda *a:pytest.fail('unchanged evidence must not fsync again'))
    assert r.inspect()['exit_code']==7
    raw[0]['State']['ExitCode']=0
    with pytest.raises(ValueError,match='changed'):r.inspect()
    raw[0]=None
    assert r.inspect()['exit_code']==7
