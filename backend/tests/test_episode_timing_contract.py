import json
import sys
from datetime import datetime,timezone
import pytest
from runner.episode_contract import validate_event
from runner import episode_timing as timing
from runner.sdk import run,EvaluationClient,EpisodeResult

START={'episode_id':1,'seed':0,'status':'STARTED'}
STAMP='2026-01-01T00:00:00+00:00'
BASE={'schema':1,'started_at':STAMP}
FINAL={**BASE,'finished_at':STAMP,'duration_seconds':0,'clock_adjusted':False}

@pytest.mark.parametrize('value',[
 None,{},dict(BASE,schema=True),dict(BASE,schema=2),dict(BASE,extra=1),dict(BASE,started_at=0),
 dict(BASE,started_at='invalid'),dict(BASE,started_at='2026-01-01'),dict(BASE,started_at='2026-01-01T00:00:00+01:00'),dict(BASE,started_at='x'*41),
])
def test_bad_start(value):
    with pytest.raises(ValueError):validate_event({**START,'timing':value})

@pytest.mark.parametrize('value',[-1,True,float('inf'),float('nan'),315576001,'2'])
def test_invalid_duration(value):
    with pytest.raises(ValueError):validate_event({**START,'status':'ERROR','error':'failed','timing':dict(FINAL,duration_seconds=value)})

def test_clock_jump_and_error_contract(monkeypatch):
    times=iter([10,12.5]);stamps=iter([STAMP,'2025-12-31T23:59:50+00:00'])
    monkeypatch.setattr(timing.time,'monotonic',lambda:next(times));monkeypatch.setattr(timing,'stamp',lambda:next(stamps))
    timer=timing.Timer()
    validate_event({**START,'timing':timer.event()})
    final=timer.event(True)
    assert final['duration_seconds']==2.5 and final['clock_adjusted']
    validate_event({**START,'status':'ERROR','error':'failed','timing':final})
    with pytest.raises(ValueError):validate_event({**START,'status':'ERROR','error':'failed','timing':dict(FINAL,clock_adjusted=1)})

@pytest.mark.parametrize('mode,enabled,expect',[(None,True,False),('direct',True,False),('sdk-command',False,False),('sdk-command',True,True)])
def test_negotiation_whole_task_and_error(mode,enabled,expect,tmp_path,monkeypatch):
    monkeypatch.setenv('MINI_EVAL_WORK_DIR',str(tmp_path))
    class Client(EvaluationClient):
        def run_episode(self,*args):return EpisodeResult(True)
    run(Client(),{'episodes':1,'seed':0,'template':{'integration_mode':mode},'_episode_timing_v1':enabled},tmp_path/'out')
    events=[json.loads(x) for x in (tmp_path/'episodes.jsonl').read_text().splitlines()]
    assert all(('timing' in e)==expect for e in events)
    if expect:
        assert events[0]['timing']['started_at']==events[1]['timing']['started_at']
        assert events[1]['timing']['duration_seconds']>=0

@pytest.mark.parametrize('code',[0,3])
def test_real_process_success_failure_without_logs(code,tmp_path,monkeypatch):
    import subprocess
    monkeypatch.setenv('MINI_EVAL_WORK_DIR',str(tmp_path))
    class Client(EvaluationClient):
        def run_episode(self,*args):
            self.process_exit_code=subprocess.run([sys.executable,'-c',f'raise SystemExit({code})']).returncode
            if code:raise RuntimeError('failed')
            return EpisodeResult(True)
    config={'episodes':2,'seed':0,'_episode_timing_v1':True,'template':{'execution_mode':'host','host_runtime':{'episode_launch':'per_episode','episode_error_policy':'continue'}}}
    if code:
        with pytest.raises(RuntimeError):run(Client(),config,tmp_path/'out')
    else:run(Client(),config,tmp_path/'out')
    events=[json.loads(x) for x in (tmp_path/'episodes.jsonl').read_text().splitlines()]
    assert len(events)==4
    for event in events[1::2]:
        assert event['exit_code']==code and event['timing']['duration_seconds']>0

@pytest.mark.parametrize('behavior',['good','missing','multiple','invalid','nonzero'])
@pytest.mark.parametrize('policy',['continue','stop'])
def test_actual_direct_adapter_timing_on_result_errors(tmp_path,behavior,policy):
    from backend.tests.test_host_shell_episode import client
    script=tmp_path/'business.py'
    line="print('{\"final\":{\"success_rate\":1,\"episode_count\":1,\"runtime\":0}}')"
    script.write_text({'good':line,'missing':'pass','multiple':line+'\n'+line,'invalid':"print('{\"final\":{}}')",'nonzero':'raise SystemExit(3)'}[behavior])
    result,events,_,_=client(tmp_path,script,episodes=2,timing=True,template_extra={'host_runtime':{'command_mode':'exec','episode_launch':'per_episode','episode_error_policy':policy}})
    assert (result.returncode==0)==(behavior=='good')
    assert len(events)==(4 if policy=='continue' or behavior=='good' else 2)
    for i in range(0,len(events),2):
        assert events[i+1]['timing']['duration_seconds']>=0
        assert events[i]['timing']['started_at']==events[i+1]['timing']['started_at']
        assert events[i+1]['exit_code']==(3 if behavior=='nonzero' else 0)


def test_preparation_error_is_timed(tmp_path,monkeypatch):
    monkeypatch.setenv('MINI_EVAL_WORK_DIR',str(tmp_path))
    class Client(EvaluationClient):
        def run_episode(self,*args):raise AssertionError('must fail before invocation')
    with pytest.raises(KeyError):run(Client(),{'episodes':1,'seed':0,'_episode_timing_v1':True,'_artifact_layout':'batch-v1','template':{'integration_mode':'sdk-command'}},tmp_path/'out')
    events=[json.loads(x) for x in (tmp_path/'episodes.jsonl').read_text().splitlines()]
    assert events[-1]['status']=='ERROR' and events[-1]['timing']['duration_seconds']>=0
