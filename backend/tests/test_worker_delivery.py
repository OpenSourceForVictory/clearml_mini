"""Durable event/log cursors under partial writes, response loss and quota rejection."""
import json
import uuid
from types import SimpleNamespace

import pytest
from worker.client import Worker,APIError
from worker.workspace import execution_dirs
from runner.sdk import EVENT_PREFIX


@pytest.fixture
def delivery(tmp_path):
    worker=Worker({},tmp_path/'state');worker.slot_id='slot';execution=str(uuid.uuid4())
    worker.state={'offset':0,'sequence':0,'pending':None};worker.save()
    events=[];worker.api=SimpleNamespace(post=lambda path,body:events.append((path,body)))
    return worker,execution,events


def test_journal_partial_line_and_bounded_batch(delivery):
    worker,execution,events=delivery
    path=execution_dirs(worker.workspace,execution)/'work/episodes.jsonl'
    lines=[json.dumps({'episode':i}).encode()+b'\n' for i in range(18)]
    path.write_bytes(b''.join(lines[:17])+lines[17][:-1])
    assert worker.send_episode_journal(execution) is False and len(events)==16
    assert worker.state['episode_file_offset']==len(b''.join(lines[:16]))
    assert worker.send_episode_journal(execution) is True and len(events)==17
    with path.open('ab') as stream:stream.write(b'\n')
    worker.send_episode_journal(execution)
    assert len(events)==18 and events[-1][1]=={'episode':17}


@pytest.mark.parametrize('terminated',[True,False])
def test_oversize_journal_does_not_advance_cursor(delivery,terminated):
    worker,execution,events=delivery
    path=execution_dirs(worker.workspace,execution)/'work/episodes.jsonl'
    path.write_bytes(b'x'*16385+(b'\n' if terminated else b''))
    with pytest.raises(RuntimeError,match='size limit'):worker.send_episode_journal(execution)
    assert not events and worker.state.get('episode_file_offset',0)==0


def test_episode_ack_loss_retries_same_event(delivery):
    worker,execution,events=delivery
    output='diagnostic\n'+EVENT_PREFIX+'{"episode":1}\n'
    def fail(path,body):events.append((path,body));raise TimeoutError('lost acknowledgement')
    worker.api.post=fail
    with pytest.raises(TimeoutError):worker.send_episodes(execution,output)
    assert worker.state['episode_offset']==len('diagnostic\n')
    worker.api.post=lambda path,body:events.append((path,body))
    assert worker.send_episodes(execution,output)
    assert events[0]==events[1]
    assert worker.state['episode_offset']==len(output)


def test_episode_log_batch_limit_and_partial_line(delivery):
    worker,execution,events=delivery
    line=EVENT_PREFIX+'{"episode":1}\n'
    assert worker.send_episodes(execution,line*17+'partial') is False
    assert len(events)==16
    assert worker.send_episodes(execution,line*17+'partial') is True
    assert len(events)==17 and worker.state['episode_offset']==len(line*17)


@pytest.mark.parametrize('status',[413,500])
def test_log_rejection_quota_vs_retry(delivery,status):
    worker,execution,events=delivery
    def reject(path,body):raise APIError(status,'rejected')
    worker.api.post=reject
    if status==413:
        assert worker.send_logs(execution,'hello') is True
        assert worker.state['pending'] is None and worker.state['offset']==5
    else:
        with pytest.raises(APIError):worker.send_logs(execution,'hello')
        assert worker.state['pending']=={'sequence':0,'content':'hello'} and worker.state['offset']==0
    assert json.loads(worker.path.read_text())==worker.state
