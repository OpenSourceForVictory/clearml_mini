"""Concurrent registration, late replies and real pathname replacement races."""
import os
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from sqlalchemy import select

from backend.app.core import EvaluationBatch, ExecutionLogCatalog, Job, Run, RunLog
from backend.tests.test_execution import env  # noqa: F401
from backend.tests.test_execution_logs_postgres import pg  # noqa: F401
from backend.tests.test_shared_artifact_lifecycle import shared_pg  # noqa: F401
from backend.tests.test_process_log_primitives import log, records  # noqa: F401
from worker.process_log_reader import registered_scope, read_page


def test_catalog_concurrent_replay_terminal_update_and_delete_reject_late_messages(shared_pg):
    value, engine = shared_pg
    main, client, auth, token, job, task, *_ = value
    body = dict(scope='episode', episode_id=1, generation=str(uuid4()),
                root_path='/old/runs/' + task['execution_id'] + '/logs')
    endpoint = '/api/v1/worker/executions/' + task['execution_id'] + '/log-catalog'
    with main.SessionLocal() as session:
        session.add(RunLog(run_id=task['run_id'], sequence=0, content='legacy preserved'))
        session.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: client.post(endpoint, headers=token, json=body), range(2)))
    assert all(response.status_code == 200 for response in replies)
    assert replies[0].json()['id'] == replies[1].json()['id']
    catalog = replies[0].json()['id']
    with main.SessionLocal() as session:
        assert len(session.scalars(select(ExecutionLogCatalog)).all()) == 1
        assert session.get(RunLog, (task['run_id'], 0)).content == 'legacy preserved'
        session.add(EvaluationBatch(id='logs-delete', owner_id='alice', title='logs-delete'))
        session.commit()
        session.get(Job, job).batch_id = 'logs-delete'
        session.get(Job, job).status = 'FAILED'
        session.get(Run, task['run_id']).status = 'FAILED'
        session.get(Run, task['run_id']).cleanup_confirmed = True
        session.commit()
    assert client.post(endpoint, headers=token, json={**body, 'status': 'closed'}).status_code == 200
    assert client.post(endpoint, headers=token, json={**body, 'episode_id': 2, 'status': 'closed'}).status_code == 409
    reads = f'/api/v1/jobs/{job}/attempts/{task["run_id"]}/log-reads'
    response = client.post(reads, headers=auth['alice'], json={'log_id': catalog})
    assert response.status_code == 202, response.text
    access = response.json()['read_id']
    request = client.post('/api/v1/worker/log-reads/claim', headers=token).json()['requests'][0]
    assert client.delete('/api/v1/batches/logs-delete', headers=auth['alice']).status_code == 200
    response = client.post('/api/v1/worker/log-reads/' + request['id'] + '/reply', headers=token,
                           json={'claim': request['claim'], 'generation': body['generation'],
                                 'status': 'ready', 'page': {'text': 'late'}})
    assert response.status_code == 404, response.text
    assert client.post(endpoint, headers=token, json={**body, 'status': 'closed'}).status_code == 404
    assert client.get('/api/v1/log-reads/' + access, headers=auth['alice']).status_code == 404
    with main.SessionLocal() as session:
        assert not session.scalars(select(ExecutionLogCatalog)).all()
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize('boundary', ['scope', 'segment'])
def test_symlink_swap_at_open_never_reads_external_log(log, tmp_path, monkeypatch, boundary):
    root, scope, write, read = log
    segment = write(records('owned log'))
    outside = tmp_path / 'external'
    outside.mkdir()
    external_bytes = b''.join(records('outside secret'))
    (outside / segment.name).write_bytes(external_bytes)
    original = os.open
    swapped = False

    def swap(path, flags, *args, **kwargs):
        nonlocal swapped
        target = 'business' if boundary == 'scope' else segment.name
        if path == target and not swapped:
            swapped = True
            selected = scope if boundary == 'scope' else scope / segment.name
            selected.rename(selected.with_name(selected.name + '.old'))
            selected.symlink_to(outside if boundary == 'scope' else outside / segment.name)
        return original(path, flags, *args, **kwargs)

    monkeypatch.setattr(os, 'open', swap)
    with pytest.raises((OSError, ValueError)):
        read([segment])
    assert swapped
    assert (outside / segment.name).read_bytes() == external_bytes


def test_pinned_scope_remains_on_original_directory_when_visible_path_is_replaced(log, tmp_path):
    root, scope, write, _ = log
    segment = write(records('owned log'))
    original_identity = (root.stat().st_dev, root.stat().st_ino)
    outside = tmp_path / 'external'
    (outside / 'business').mkdir(parents=True)
    (outside / 'business' / segment.name).write_bytes(b''.join(records('outside secret')))
    with registered_scope(str(root), original_identity, 'business') as descriptor:
        root.rename(tmp_path / 'pinned-old')
        root.symlink_to(outside)
        page = read_page(descriptor, str(uuid4()), 'business', str(uuid4()), [segment])
    assert [row['text'] for row in page['records']] == ['owned log']
    with pytest.raises((OSError, ValueError)):
        with registered_scope(str(root), original_identity, 'business'):
            pytest.fail('replaced root must not be accepted for a subsequent read')
