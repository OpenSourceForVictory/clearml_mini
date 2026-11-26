import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import text

from backend.tests import control_plane_metrics as metrics
from backend.tests.test_execution_logs_postgres import pg


def test_database_sampler_records_waits_and_wal(pg):
    with pg[0].begin() as connection:
        connection.execute(text('CREATE TABLE evaluation_runs (lease_expires_at timestamptz, status text)'))
        connection.execute(text("INSERT INTO evaluation_runs VALUES (now()-interval '1 second', 'RUNNING')"))
    report = metrics.sample(pg[0])
    assert report['waiting_locks'] == 0
    assert report['cluster_wal_bytes'] >= 0
    assert report['database']['deadlocks'] == 0
    assert report['sample_seconds'] >= 0
    assert report['expired_leases'] == 1


def test_sampler_failure_cancels_load_and_runs_cleanup(monkeypatch):
    cleaned = []
    def fail(*args):
        raise RuntimeError('test sampler failure')
    monkeypatch.setattr(metrics, 'sample', fail)
    async def work():
        try:
            await asyncio.sleep(30)
        finally:
            cleaned.append(True)
    with pytest.raises(RuntimeError, match='sampler failure'):
        asyncio.run(metrics.observe(work(), None, interval=.001))
    assert cleaned == [True]


def test_sampler_preserves_load_result_and_final_sample(monkeypatch):
    calls = []
    def sample(*args):
        calls.append(True)
        return {'waiting_locks':0}
    monkeypatch.setattr(metrics, 'sample', sample)
    async def work():
        await asyncio.sleep(.01)
        return {'passed':True}
    result = asyncio.run(metrics.observe(work(), None, interval=.005))
    assert result['passed']
    assert len(result['database_samples']) == len(calls) >= 2
    assert result['database_samples'][-1]['elapsed_seconds'] >= .01


def test_load_boundary_separates_lease_expiry_from_log_drain(monkeypatch):
    calls = []
    def sample(*args):
        calls.append(True)
        return {'expired_leases': 0 if len(calls) < 5 else 7}
    monkeypatch.setattr(metrics, 'sample', sample)
    async def work():
        await asyncio.sleep(.025)
        return {'passed': True}
    result = asyncio.run(metrics.observe(work(), None, interval=.004, load_seconds=.012))
    rows = result['database_samples']
    assert any(row['phase'] == 'boundary' for row in rows)
    assert any(row['phase'] == 'drain' and row['expired_leases'] == 7 for row in rows)
    assert metrics.expired_leases_during_load(rows) == 0
    assert metrics.expired_leases_during_load([{'phase':'load', 'expired_leases':2}, *rows]) == 2


def test_peak_execution_overlap_uses_completed_spans_and_half_open_boundaries():
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    second = timedelta(seconds=1)
    spans = [(start, start+3*second), (start+second, start+2*second),
             (start+3*second, start+4*second), (None, start+second),
             (start+5*second, None)]
    assert metrics.peak_execution_overlap(spans) == 2
    assert metrics.full_execution_overlap_seconds(spans) == 0
    assert metrics.full_execution_overlap_seconds([(start, start+3*second),
                                                   (start+second, start+2*second)]) == 1
    assert metrics.peak_execution_overlap([]) == 0


def test_outage_restores_role_and_records_timing():
    calls = []
    async def work():
        await asyncio.sleep(.03)
        return {'passed':True}
    report = asyncio.run(metrics.with_outage(work(), lambda: calls.append('stop'),
                        lambda: calls.append('restart'), after=.001, duration=.005))
    assert calls == ['stop','restart']
    assert report['passed']
    evidence = report['during_load_outage']
    assert evidence['restarted_at_seconds'] >= evidence['stopped_at_seconds'] + .005


def test_outage_failure_waits_for_stop_before_restoring():
    import time
    import threading
    calls, stopping = [], threading.Event()
    def stop():
        stopping.set()
        time.sleep(.03)
        calls.append('stopped')
    async def work():
        while not stopping.is_set():
            await asyncio.sleep(.001)
        raise RuntimeError('test load failed while role stopping')
    with pytest.raises(RuntimeError, match='load failed'):
        asyncio.run(metrics.with_outage(work(), stop, lambda: calls.append('restart'), after=0, duration=10))
    assert calls == ['stopped','restart']
