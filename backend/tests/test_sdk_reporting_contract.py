"""SDK/API boundary regressions; no production service or GPU is required."""
import json
from datetime import timedelta

import pytest

from backend.tests.test_execution import env, node
from backend.tests.test_cs_protocol import prepare
from backend.app.core import Job, Run, now
from runner.episode_contract import EpisodeExecutionError, result_error_code
from runner.sdk import EvaluationClient, EpisodeResult, EVENT_PREFIX, run


def reporting(env, episodes=2):
    main, client, auth = env
    _, token, _, job_id, task = prepare(env)
    with main.SessionLocal() as session:
        job = session.get(Job, job_id)
        job.episodes = episodes
        job.client_spec = {**job.client_spec, 'template': {**job.client_spec['template'], 'episode_reporting': True}}
        session.commit()
    return client, token, '/api/v1/worker/executions/'+task['execution_id'], f'/api/v1/jobs/{job_id}/attempts/{task["run_id"]}/episodes', task


def start(i=1):
    return {'episode_id': i, 'seed': 6+i, 'status': 'STARTED'}


def end(i=1, success=False):
    return {**start(i), 'status': 'COMPLETED', 'success': success, 'metrics': {}, 'runtime': .1, 'artifacts': []}


def test_partial_results_keep_planned_count_and_completed_denominator(env):
    c, token, base, view, _ = reporting(env, 100)
    for i in range(1, 81):
        assert c.post(base+'/episodes', headers=token, json=start(i)).status_code == 200
        assert c.post(base+'/episodes', headers=token, json=end(i, i<=60)).status_code == 200
    result = c.get(view, headers=env[2]['alice']).json()
    assert result['planned'] == 100
    assert result['progress'] == {'completed': 80, 'successes': 60, 'errors': 0, 'running': [], 'success_rate': .75}
    response = c.post(base+'/complete', headers=token, json={'status':'SUCCEEDED','metrics':{'success_rate':.75,'episode_count':100,'runtime':1}})
    assert response.status_code == 400


def test_all_business_failures_still_allow_successful_execution(env):
    c, token, base, view, _ = reporting(env)
    for i in (1, 2):
        assert c.post(base+'/episodes', headers=token, json=start(i)).status_code == 200
        assert c.post(base+'/episodes', headers=token, json=end(i)).status_code == 200
    assert c.post(base+'/complete', headers=token, json={'status':'SUCCEEDED','metrics':{'success_rate':0,'episode_count':2,'runtime':1}}).status_code == 200
    assert c.get(view, headers=env[2]['alice']).json()['progress']['success_rate'] == 0


@pytest.mark.parametrize('event', [
    {**start(), 'episode_id':3}, {**start(), 'seed':99},
    {**start(), 'episode_id':True}, {**start(), 'unexpected':'field'},
    {**end(), 'success':1}, {**end(), 'runtime':-1},
    {**end(), 'metrics':{'score':'good'}}, {**end(), 'artifacts':['../secret']},
])
def test_invalid_events_do_not_change_progress(env, event):
    c, token, base, view, _ = reporting(env)
    assert c.post(base+'/episodes', headers=token, json=event).status_code == 400
    progress = c.get(view, headers=env[2]['alice']).json()['progress']
    assert progress == {'completed':0,'successes':0,'errors':0,'running':[],'success_rate':None}


def test_expired_and_foreign_workers_cannot_add_results(env):
    c, token, base, view, task = reporting(env)
    foreign, _ = node(env, name='foreign')
    assert c.post(base+'/episodes', headers=foreign, json=start()).status_code == 404
    with env[0].SessionLocal() as session:
        session.get(Run, task['run_id']).lease_expires_at = now()-timedelta(seconds=1)
        session.commit()
    assert c.post(base+'/episodes', headers=token, json=start()).status_code == 409
    assert c.get(view, headers=env[2]['alice']).json()['progress']['running'] == []


@pytest.mark.parametrize('result', [None, EpisodeResult(1), EpisodeResult(True, {'score':float('nan')}), EpisodeResult(True, artifacts=['../secret'])])
def test_invalid_sdk_result_never_emits_final_success(tmp_path, capsys, monkeypatch, result):
    monkeypatch.setenv('MINI_EVAL_WORK_DIR', str(tmp_path))
    class Client(EvaluationClient):
        closed = False
        def run_episode(self, *args): return result
        def teardown(self): self.closed = True
    client = Client()
    with pytest.raises(EpisodeExecutionError, match='episode_result_invalid_episode_result'):
        run(client, {'episodes':2,'seed':7}, tmp_path/'output')
    output = capsys.readouterr().out
    assert '"final"' not in output and client.closed
    events = [json.loads(line) for line in (tmp_path/'episodes.jsonl').read_text().splitlines()]
    assert [event['status'] for event in events] == ['STARTED','ERROR']
    assert result_error_code(events[-1]['error'])=='invalid_episode_result'
    assert [json.loads(line[len(EVENT_PREFIX):]) for line in output.splitlines() if line.startswith(EVENT_PREFIX)] == events


def test_torn_event_journal_waits_for_full_line_and_ack(tmp_path):
    from worker.client import Worker
    from worker.workspace import execution_dirs
    import uuid
    execution = str(uuid.uuid4())
    worker = Worker({}, tmp_path/'state')
    worker.workspace = tmp_path/'workspace'
    directory = execution_dirs(worker.workspace, execution)/'work'
    directory.mkdir(parents=True, exist_ok=True)
    data = json.dumps(start()).encode()
    path = directory/'episodes.jsonl'; path.write_bytes(data)
    worker.state = {}
    calls = []
    class API:
        def post(self, url, event):
            calls.append(event)
            if len(calls) == 1: raise TimeoutError('response lost')
    worker.api = API()
    worker.send_episode_journal(execution)
    assert calls == [] and worker.state.get('episode_file_offset',0) == 0
    path.write_bytes(data+b'\n')
    with pytest.raises(TimeoutError): worker.send_episode_journal(execution)
    assert worker.state.get('episode_file_offset',0) == 0
    worker.send_episode_journal(execution)
    worker.send_episode_journal(execution)
    assert calls == [start(),start()]
    assert worker.state['episode_file_offset'] == len(data)+1


def test_error_then_later_completed_episodes_remain_visible_on_failed_task(env):
    c,token,base,view,_=reporting(env,3)
    for i in (1,2,3):
        assert c.post(base+'/episodes',headers=token,json=start(i)).status_code==200
        result=({**start(i),'status':'ERROR','error':'business episode failed','exit_code':1}
                if i==1 else {**end(i,True),'exit_code':0})
        assert c.post(base+'/episodes',headers=token,json=result).status_code==200
    response=c.post(base+'/complete',headers=token,json={'status':'FAILED','error':'1 of 3 episodes had execution errors','exit_code':2})
    assert response.status_code==200,response.text
    detail=c.get(view,headers=env[2]['alice']).json()
    assert detail['run_status']=='FAILED'
    assert detail['progress']['errors']==1 and detail['progress']['completed']==2
    assert [item['result']['exit_code'] for item in detail['items']]==[1,0,0]
