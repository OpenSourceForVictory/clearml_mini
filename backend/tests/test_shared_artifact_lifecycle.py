"""Lifecycle and small-pool checks for metadata-only shared artifacts."""
import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.app.core import Base, EvaluationBatch, Job, Pool, Run, SharedArtifactRoot, User
from backend.tests.test_execution import env  # noqa: F401
from backend.tests.test_execution_logs_postgres import pg  # noqa: F401
from backend.tests.test_shared_artifacts import shared, route  # noqa: F401


@pytest.fixture
def shared_pg(env, pg, tmp_path, monkeypatch):
    main, _, _ = env
    _, url = pg
    records = {}
    with main.SessionLocal() as session:
        for model in (User, Pool):
            records[model] = [
                {column.name: getattr(row, column.name) for column in model.__table__.columns}
                for row in session.scalars(select(model))
            ]
    engine = create_engine(url, pool_size=2, max_overflow=0, pool_timeout=0.5)
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        for model, rows in records.items():
            connection.execute(model.__table__.insert(), rows)
    monkeypatch.setattr(main, 'SessionLocal', sessionmaker(engine, expire_on_commit=False))
    try:
        yield shared.__wrapped__(env, tmp_path, monkeypatch), engine
    finally:
        engine.dispose()


def registration(shared):
    task = shared[5]
    return (f'/api/v1/worker/executions/{task["execution_id"]}/shared-artifacts',
            {'output_root': f'/node/share/machine/runs/{task["execution_id"]}/output'})


def test_real_small_pool_slow_shared_reads_leave_heartbeat_and_cancel_available(shared_pg, monkeypatch):
    from backend.app import shared_file_io

    value, engine = shared_pg
    main, client, auth, token, job, task, *_ = value
    started = threading.Event()
    release = threading.Event()
    lock = threading.Lock()
    readers = 0
    original = shared_file_io.read

    async def delayed(*args, **kwargs):
        nonlocal readers
        with lock:
            readers += 1
            if readers == 2:
                started.set()
        assert await asyncio.to_thread(release.wait, 10), 'test did not release shared reads'
        return await original(*args, **kwargs)

    monkeypatch.setattr(shared_file_io, 'read', delayed)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(client.get, route(value), headers=auth['alice']) for _ in range(2)]
        try:
            assert started.wait(5), 'both HTTP requests must reach filesystem I/O'
            assert engine.pool.checkedout() == 0
            with main.SessionLocal() as session:
                slot = session.get(Run, task['run_id']).slot_id
            response = client.post('/api/v1/worker/heartbeat', headers=token,
                                   json={'slot_id': slot, 'execution_id': task['execution_id']})
            assert response.status_code == 200, response.text
            response = client.post('/api/v1/jobs/' + job + '/cancel', headers=auth['alice'])
            assert response.status_code == 202, response.text
        finally:
            release.set()
        for future in futures:
            response = future.result(timeout=10)
            assert response.status_code == 200, response.text
            assert any(item['name'] == 'result.json' for item in response.json()['items'])
    assert engine.pool.checkedout() == 0


@pytest.mark.parametrize('status', ['FAILED', 'CANCELLED', 'SUCCEEDED'])
def test_terminal_registration_replay_and_deleted_batch_cannot_revive(shared_pg, status):
    value, engine = shared_pg
    main, client, auth, token, job, task, output, _ = value
    endpoint, body = registration(value)
    with main.SessionLocal() as session:
        session.add(EvaluationBatch(id='artifact-delete', owner_id='alice', title='artifact-delete'))
        session.commit()
        session.get(Job, job).batch_id = 'artifact-delete'
        session.get(Job, job).status = status
        run = session.get(Run, task['run_id'])
        run.status = status
        run.cleanup_confirmed = True
        session.commit()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda _: client.post(endpoint, headers=token, json=body), range(2)))
    assert all(response.status_code == 200 for response in responses)
    with main.SessionLocal() as session:
        assert len(session.scalars(select(SharedArtifactRoot)).all()) == 1
        assert session.get(Run, task['run_id']).status == status
    response = client.delete('/api/v1/batches/artifact-delete', headers=auth['alice'])
    assert response.status_code == 200, response.text
    assert client.post(endpoint, headers=token, json=body).status_code == 404
    assert client.get(route(value), headers=auth['alice']).status_code == 404
    with main.SessionLocal() as session:
        assert not session.scalars(select(SharedArtifactRoot)).all()
        assert session.get(Run, task['run_id']) is None
    # Database deletion never treats shared business files as owned disposable work files.
    assert (output / 'result.json').read_text() == '{"success":true}'
    assert engine.pool.checkedout() == 0
