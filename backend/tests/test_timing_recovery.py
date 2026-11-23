import json,uuid
import pytest
from backend.tests.test_worker_execution_recovery import execution
from worker.client import Worker
from worker.workspace import execution_dirs


@pytest.mark.parametrize('stored',[None,False,True])
def test_worker_upgrade_preserves_execution_timing_negotiation(execution,monkeypatch,stored):
    worker,task,events=execution
    task['episode_timing_v1']=True
    old=dict(task);old.pop('episode_timing_v1')
    if stored is not None:old['episode_timing_v1']=stored
    worker.state={'task':old,'created':False,'offset':0,'sequence':0,'pending':None}
    checks=iter([None,{'State':{'Status':'exited','Running':False,'ExitCode':0}}])
    monkeypatch.setattr(worker,'inspect',lambda e:next(checks))
    worker.execute(task)
    created=next(body for path,body in events if path=='create')
    assert created['episode_timing_v1'] is bool(stored)


def test_timed_journal_disconnect_restart_replays_exact_event(tmp_path):
    worker=Worker({},tmp_path/'state');worker.workspace=tmp_path/'workspace'
    identity=str(uuid.uuid4());directory=execution_dirs(worker.workspace,identity)/'work';directory.mkdir(parents=True,exist_ok=True)
    event={'episode_id':1,'seed':0,'status':'STARTED','timing':{'schema':1,'started_at':'2026-01-01T00:00:00+00:00'}}
    (directory/'episodes.jsonl').write_text(json.dumps(event)+'\n')
    worker.state={'task':{'episode_timing_v1':True}};worker.save();calls=[]
    def lost(path,body):calls.append(body);raise TimeoutError('response lost')
    worker.api.post=lost
    with pytest.raises(TimeoutError):worker.send_episode_journal(identity)
    recovered=Worker({},worker.path);recovered.workspace=worker.workspace
    recovered.api.post=lambda path,body:calls.append(body)
    recovered.send_episode_journal(identity);recovered.send_episode_journal(identity)
    assert calls==[event,event]
    assert recovered.state['task']['episode_timing_v1'] is True
