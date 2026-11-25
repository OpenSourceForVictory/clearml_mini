import copy
import json
from pathlib import Path

import pytest

from backend.tests.test_worker_recovery_execution import case
from worker.recovery_start import start_or_recover


@pytest.mark.parametrize('fault',[None,'lost_ack','bad_ack','remove_failed','wrong_marker','running'])
def test_unstarted_runtime_cleanup_only_after_original_stop(case,monkeypatch,fault):
    worker=case.worker;post=worker.api.post
    def api(path,body=None):
        if path.endswith('/recovery/not-started'):
            case.calls.append('unstarted')
            if fault=='lost_ack':raise TimeoutError('lost cleanup response')
            return {} if fault=='bad_ack' else {'execution_id':worker.state['task']['execution_id'],
                'generation':'g','status':'CANCELLED','cleanup_confirmed':True,'duplicate':False}
        value=post(path,body)
        if path.endswith('/recovery/prepare'):
            if fault=='wrong_marker':case.raw[0]['Config']['Labels']['mini-eval.launch']='wrong'
            if fault=='running':case.raw[0]['State'].update(Status='running',Running=True)
            value.update(action='stop',reason='cancelled')
        return value
    worker.api.post=api
    def remove(*args,**kwargs):
        assert args==('rm','a'*64)
        if fault=='remove_failed':raise RuntimeError('failed to remove')
        case.calls.append('unstarted-remove');case.raw[0]=None
    monkeypatch.setattr('worker.client.docker',remove)
    if fault:
        with pytest.raises((TimeoutError,ValueError,RuntimeError)):worker.execute(worker.state['task'])
        assert worker.state is not None
        if fault in ('wrong_marker','running','remove_failed'):assert 'unstarted' not in case.calls
    else:
        worker.execute(worker.state['task'])
        assert worker.state is None and json.loads(worker.path.read_text()) is None
        assert case.calls.index('unstarted-remove')<case.calls.index('unstarted')
    assert 'start' not in case.calls and 'stop' not in case.calls


def test_unstarted_cleanup_refuses_attempted_launch(case):
    from types import SimpleNamespace
    from worker.recovery_unstarted import settle
    record={'phase':'attempted','reply':{'action':'stop','reason':'cancelled'},
            'started_at':1,'identity':None}
    with pytest.raises(RuntimeError,match='may have started'):
        settle(case.worker,SimpleNamespace(record=record))
    assert case.worker.state is not None and not case.calls


def test_unstarted_cleanup_requires_confirmed_removal(case,monkeypatch):
    post=case.worker.api.post
    def api(path,body=None):
        value=post(path,body);value.update(action='stop',reason='cancelled');return value
    case.worker.api.post=api
    monkeypatch.setattr('worker.client.docker',lambda *a,**kw:None)
    with pytest.raises(RuntimeError,match='cleanup not confirmed'):
        case.worker.execute(case.worker.state['task'])
    assert case.worker.state is not None and 'start' not in case.calls
    assert not any(isinstance(c,tuple) and c[0].endswith('/recovery/not-started') for c in case.calls)
