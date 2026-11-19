# ruff: noqa: F401, F811 -- imported pytest fixtures are resolved by name
"""Bounded mailbox load and mixed-version negotiation; no production services."""

from datetime import timedelta
import time

import pytest
from sqlalchemy import select, func
from backend.tests.test_execution import env, claim
from backend.tests.test_process_log_api import setup
from backend.tests.test_execution_logs_postgres import pg
from backend.app.core import User, Slot, LogReadRequest, now


def test_offline_node_burst_has_global_bound_and_releases_connections(
    env, monkeypatch, parallel=False
):
    from backend.app import process_logs

    main, c, auth, token, job, task, registration, catalog, route = setup(env)
    headers = []
    with main.SessionLocal() as s:
        for i in range(17):
            s.add(
                User(
                    id=f"reader-{i}",
                    username=f"reader-{i}",
                    role="admin",
                    password_hash="unused",
                )
            )
        s.commit()
    for i in range(17):
        headers.append(
            {
                "Authorization": "Bearer "
                + main.login_token(f"reader-{i}", int(time.time()))["access_token"]
            }
        )
    url = f"/api/v1/jobs/{job}/attempts/{task['run_id']}/log-reads"
    frozen = now()
    monkeypatch.setattr(process_logs, "now", lambda: frozen)
    durations = []

    # An offline node never answers, so requests accumulate until the hard cap.
    def request(i):
        begin = time.monotonic()
        response = c.post(
            url, headers=headers[i // 8], json={"log_id": catalog["id"], "until": i}
        )
        durations.append(time.monotonic() - begin)
        assert response.status_code == 202, response.text
        if not parallel:
            assert main.SessionLocal.kw["bind"].pool.checkedout() == 0

    if parallel:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(request, range(128)))
    else:
        for i in range(128):
            request(i)
    assert main.SessionLocal.kw["bind"].pool.checkedout() == 0
    overflow = c.post(
        url, headers=headers[-1], json={"log_id": catalog["id"], "until": 128}
    )
    assert overflow.status_code == 429 and overflow.headers["Retry-After"] == "2"
    with main.SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(LogReadRequest)) == 128
    assert (
        c.post(
            "/api/v1/worker/heartbeat",
            headers=token,
            json={
                "slot_id": task["slot_id"] if "slot_id" in task else _slot(main, task),
                "execution_id": task["execution_id"],
            },
        ).status_code
        == 200
    )
    assert (
        c.post("/api/v1/jobs/" + job + "/cancel", headers=auth["alice"]).status_code
        == 202
    )
    current = frozen
    monkeypatch.setattr(process_logs, "now", lambda: current + timedelta(seconds=31))
    accepted = c.post(
        url, headers=headers[-1], json={"log_id": catalog["id"], "until": 129}
    )
    assert accepted.status_code == 202, accepted.text
    with main.SessionLocal() as s:
        assert s.scalar(select(func.count()).select_from(LogReadRequest)) == 1
    print(
        "offline-node 128 reads: max_seconds=%.4f total_seconds=%.4f"
        % (max(durations), sum(durations))
    )


def _slot(main, task):
    from backend.app.core import Run

    with main.SessionLocal() as s:
        return s.get(Run, task["run_id"]).slot_id


@pytest.mark.parametrize(
    "capabilities",
    [
        [],
        ["bounded_logs_v2"],
        ["process_logs_v1"],
        ["bounded_logs_v2", "process_logs_v1"],
    ],
)
def test_new_log_protocol_negotiates_only_complete_capability(env, capabilities):
    from backend.tests.test_image_runtime import runtime_setup
    from backend.tests.test_cs_protocol import submit_body

    main, c, auth = env
    template, token, slot, _, report = runtime_setup(env)
    with main.SessionLocal() as s:
        instance = s.get(Slot, slot)
        instance.protocol_version = 17
        instance.software = {"capabilities": capabilities}
        s.commit()
    assert (
        c.post(
            "/api/v1/worker/heartbeat",
            headers=token,
            json={"slot_id": slot, "runtime": report},
        ).status_code
        == 200
    )
    result = c.post("/api/v1/jobs", headers=auth["alice"], json=submit_body(template))
    assert result.status_code == 201, result.text
    task = claim(env, token, slot)
    assert task is not None
    assert task.get("process_logs_v1", False) == all(
        cap in capabilities for cap in ["bounded_logs_v2", "process_logs_v1"]
    )
    assert (task.get("log_protocol") == 2) == ("bounded_logs_v2" in capabilities)


@pytest.mark.parametrize("parallel", [False, True])
def test_postgres_small_pool_offline_node_burst(env, monkeypatch, parallel, pg):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from backend.app.core import Base, Pool

    _, url = pg  # Fixture creates and drops a unique temporary database.
    main, client, auth = env
    with main.SessionLocal() as s:
        users = [
            {col.name: getattr(row, col.name) for col in User.__table__.columns}
            for row in s.scalars(select(User))
        ]
        pools = [
            {col.name: getattr(row, col.name) for col in Pool.__table__.columns}
            for row in s.scalars(select(Pool))
        ]
    engine = create_engine(url, pool_size=2, max_overflow=0, pool_timeout=0.5)
    try:
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        with engine.begin() as conn:
            conn.execute(User.__table__.insert(), users)
            conn.execute(Pool.__table__.insert(), pools)
        monkeypatch.setattr(
            main, "SessionLocal", sessionmaker(engine, expire_on_commit=False)
        )
        test_offline_node_burst_has_global_bound_and_releases_connections(
            env, monkeypatch, parallel=parallel
        )
        assert engine.pool.checkedout() == 0
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()
