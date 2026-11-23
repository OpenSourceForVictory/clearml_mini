"""Overload metadata must not turn protocol rejection into an endless retry."""
import io
import threading
import urllib.error

import pytest

from worker import backpressure as bp
from worker.client import Client, APIError


@pytest.mark.parametrize('value,expected', [
    (None, None), ('', None), ('garbage', None), ('-1', None),
    ('nan', None), ('inf', None), ('1.5', None), ('1e3', None),
    ('１２', None), ('0', 0), (' 2 ', 2), ('9' * 400, None),
    ('Thu, 01 Jan 1970 00:01:40 GMT', 50),
    ('Thu, 01 Jan 1970 00:00:01 GMT', 0),
    ('Thu, 01 Jan 1970 00:01:40', None),
])
def test_retry_after(value, expected):
    assert bp.parse_retry_after(value, now=50) == expected


@pytest.mark.parametrize('status', [400, 401, 403, 404, 409, 413, 422, 500])
def test_permanent_status_is_never_retried(status):
    error = APIError(status, 'rejected')
    with pytest.raises(APIError) as raised:
        bp.Backoff().wait(error, threading.Event())
    assert raised.value is error
    assert not bp.transient(urllib.error.HTTPError('http://test', status, '', {}, None))


@pytest.mark.parametrize('status', [429, 502, 503, 504])
def test_overload_classification(status):
    assert bp.transient(APIError(status, 'busy'))
    assert bp.transient(urllib.error.HTTPError('http://test', status, '', {}, None))


@pytest.mark.parametrize('error,expected', [
    (TimeoutError(), True), (ConnectionError(), True),
    (urllib.error.URLError('offline'), True), (OSError('local disk failure'), False),
    (ValueError('invalid data'), False),
])
def test_transport_not_local_failures(error, expected):
    assert bp.transient(error) is expected


class ClockStop:
    def __init__(self):
        self.now = 0
        self.delays = []
        self.stopped = False

    def is_set(self):
        return self.stopped

    def wait(self, delay):
        self.delays.append(delay)
        self.now += delay
        return self.stopped


def test_bounded_jitter_reset_and_lease(monkeypatch):
    stop = ClockStop()
    monkeypatch.setattr(bp.time, 'monotonic', lambda: stop.now)
    monkeypatch.setattr(bp.random, 'uniform', lambda low, high: high)
    retry = bp.Backoff()
    for _ in range(30):
        assert retry.wait(APIError(503, 'busy'), stop)
    assert stop.delays[:5] == [.25, .5, 1, 2, 2]
    assert max(stop.delays) == 2
    retry.reset()
    assert retry.attempt == 0
    assert retry.wait(APIError(503, 'busy', '10000'), stop)
    assert stop.delays[-1] == 10000.25
    assert not retry.wait(APIError(503, 'busy'), stop, stop.now + .1)
    assert stop.delays[-1] == pytest.approx(.1)
    before = len(stop.delays)
    assert not retry.wait(APIError(503, 'busy'), stop, stop.now)
    stop.stopped = True
    assert not retry.wait(APIError(503, 'busy'), stop)
    assert len(stop.delays) == before


def test_claim_retry_caps_total_wait_but_keeps_retrying(monkeypatch):
    stop = ClockStop()
    monkeypatch.setattr(bp.time, 'monotonic', lambda: stop.now)
    monkeypatch.setattr(bp.random, 'uniform', lambda low, high: high)
    retry = bp.Backoff(max_total=3)
    for _ in range(20):
        assert retry.wait(APIError(503, 'busy', '60'), stop)
    assert stop.delays == [3] * 20
    assert retry.attempt == 16
    assert retry.wait(APIError(503, 'busy'), stop)
    assert stop.delays[-1] == 2


@pytest.mark.parametrize('limit', [0, -1, float('nan'), float('inf')])
def test_invalid_total_backoff_bound(limit):
    with pytest.raises(ValueError):
        bp.Backoff(max_total=limit)


def test_cancel_during_wait():
    stop = ClockStop()
    stop.wait = lambda delay: True
    assert not bp.Backoff().wait(APIError(503, 'busy'), stop)


@pytest.mark.parametrize('base,maximum', [(0, 2), (3, 2), (float('nan'), 2), (.2, float('inf'))])
def test_invalid_bounds(base, maximum):
    with pytest.raises(ValueError):
        bp.Backoff(base, maximum)


@pytest.mark.parametrize('headers,delay', [({'Retry-After': '3'}, 3), ({}, None), (None, None)])
def test_client_retains_hint_without_replaying(monkeypatch, headers, delay):
    calls = []
    def reject(request, timeout):
        calls.append(request)
        raise urllib.error.HTTPError(request.full_url, 503, 'busy', headers, io.BytesIO(b'busy'))
    monkeypatch.setattr('urllib.request.urlopen', reject)
    with pytest.raises(APIError) as raised:
        Client({'api_url': 'http://example.invalid', 'token': 'test'}).post('worker/heartbeat')
    assert raised.value.status == 503
    assert raised.value.retry_after == delay
    assert len(calls) == 1


def test_retry_after_keeps_jitter_and_never_retries_before_hint(monkeypatch):
    observed=[]
    for fraction in (0,.25,.5,.75,1):
        stop=ClockStop()
        monkeypatch.setattr(bp.time,'monotonic',lambda:stop.now)
        monkeypatch.setattr(bp.random,'uniform',lambda low,high:low+(high-low)*fraction)
        assert bp.Backoff().wait(APIError(503,'busy','1'),stop)
        observed.append(stop.delays[0])
    assert len(set(observed))==5
    assert min(observed)>1 and max(observed)<=1.25
    stop=ClockStop()
    monkeypatch.setattr(bp.time,'monotonic',lambda:stop.now)
    assert not bp.Backoff().wait(APIError(503,'busy','100'),stop,deadline=3)
    assert stop.delays==[3]
