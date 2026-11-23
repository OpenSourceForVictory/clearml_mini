"""Bounded online DB admission; scheduler and migrations do not use request deadlines."""
import asyncio
import anyio.to_thread
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
import logging
import math
import os
import re
import threading
import time

from sqlalchemy import create_engine, event
from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout
from sqlalchemy.orm import sessionmaker
from starlette.responses import JSONResponse

LOG = logging.getLogger(__name__)
if os.getenv('DB_BUDGET_DIAGNOSTICS', '0') == '1':
    LOG.setLevel(logging.DEBUG)
    if not LOG.handlers:
        LOG.addHandler(logging.StreamHandler())
    LOG.propagate = False
request_budget = ContextVar('db_request_budget', default=None)


class DatabaseBusy(Exception):
    pass


@dataclass(frozen=True)
class Settings:
    ordinary: int = 10
    control: int = 5
    observation: int = 2
    result: int = 2
    scheduler: int = 1
    processes: int = 1
    connection_budget: int = 80
    pool_timeout: float = .25
    lock_timeout: float = .5
    request_timeout: float = 4

    def __post_init__(self):
        for value in (*self.pool_sizes.values(), self.processes, self.connection_budget):
            if type(value) is not int or value < 1:
                raise ValueError('database connection budgets must be positive integers')
        if sum(self.pool_sizes.values()) * self.processes > self.connection_budget:
            raise ValueError('API database pools exceed total connection budget')
        if not all(math.isfinite(v) and v > 0 for v in (self.pool_timeout, self.lock_timeout, self.request_timeout)):
            raise ValueError('database wait budgets must be finite and positive')
        if not self.pool_timeout < self.request_timeout < 5 or not self.lock_timeout < self.request_timeout:
            raise ValueError('database wait budgets must fit within Worker HTTP timeout (5 seconds)')

    @property
    def pool_sizes(self):
        return {name: getattr(self, name) for name in
                ('ordinary', 'control', 'observation', 'result', 'scheduler')}

    @classmethod
    def environment(cls):
        return cls(**{key: cast(os.environ[name]) for key, name, cast in (
            ('ordinary', 'DB_POOL_SIZE', int), ('control', 'DB_CONTROL_POOL_SIZE', int),
            ('observation', 'DB_OBSERVATION_POOL_SIZE', int), ('result', 'DB_RESULT_POOL_SIZE', int),
            ('scheduler', 'DB_SCHEDULER_POOL_SIZE', int),
            ('processes', 'API_DB_PROCESS_COUNT', int), ('connection_budget', 'DB_CONNECTION_BUDGET', int),
            ('pool_timeout', 'DB_POOL_TIMEOUT_SECONDS', float), ('lock_timeout', 'DB_LOCK_TIMEOUT_SECONDS', float),
            ('request_timeout', 'DB_REQUEST_TIMEOUT_SECONDS', float),
        ) if name in os.environ})


def request_category(path, method='POST'):
    if method != 'POST':
        return 'ordinary'
    if path == '/api/v1/agent/heartbeat':
        return 'observation'
    if path in ('/api/v1/worker/heartbeat', '/api/v1/agent/stop-permit') or re.fullmatch(
            r'/api/v1/worker/executions/[^/]+/(?:service-health|service-probe-session|complete|cleanup)', path):
        return 'control'
    if re.fullmatch(r'/api/v1/jobs/[^/]+/cancel', path) or re.fullmatch(
            r'/api/v1/(?:batches|admin/services)/[^/]+/control', path):
        return 'control'
    if re.fullmatch(r'/api/v1/worker/executions/[^/]+/(?:episodes|task-result)', path):
        return 'result'
    return 'ordinary'


def control_path(path):
    return request_category(path) == 'control'


def busy_reason(error):
    if isinstance(error, DatabaseBusy):
        return 'request_budget'
    if isinstance(error, PoolTimeout):
        return 'connection_pool'
    if isinstance(error, DBAPIError):
        code = getattr(error.orig, 'sqlstate', None) or getattr(error.orig, 'pgcode', None)
        return {'55P03': 'lock_timeout', '57014': 'statement_timeout',
                '40P01': 'deadlock', '40001': 'serialization_retry'}.get(code)
    return None


def busy_response(reason):
    return JSONResponse({'detail': 'database busy; retry shortly', 'reason': reason},
                        status_code=503, headers={'Retry-After': '1'})


def factories(url, settings, *, isolated=False):
    """All pools have zero overflow, including the background scheduler."""
    if url.startswith('sqlite:'):
        factory = sessionmaker(create_engine(url), expire_on_commit=False)
        return ({name: factory for name in settings.pool_sizes} if isolated else (factory, factory))
    def make(size):
        engine = create_engine(url, pool_size=size, max_overflow=0, pool_timeout=settings.pool_timeout,
                               connect_args={'connect_timeout': 1})
        @event.listens_for(engine, 'before_cursor_execute')
        def deadline(connection, cursor, statement, parameters, context, executemany):
            budget = request_budget.get()
            if budget is None:
                return
            remaining = budget - time.monotonic()
            if remaining <= 0:
                raise DatabaseBusy()
            # Transaction-local settings cannot leak to the scheduler or migrate.
            # Recompute for every statement: statement_timeout alone is not an
            # aggregate request budget. No SQL text/parameters enter diagnostics.
            millis = max(1, int(remaining * 1000))
            lock_ms = min(millis, max(1, int(settings.lock_timeout * 1000)))
            cursor.execute("SELECT set_config('statement_timeout', %s, true), "
                           "set_config('lock_timeout', %s, true)", (str(millis), str(lock_ms)))
        return sessionmaker(engine, expire_on_commit=False)
    if isolated:
        return {name: make(size) for name, size in settings.pool_sizes.items()}
    return make(settings.ordinary), make(settings.control)


class Admission:
    """Reject before authentication, DB checkout and synchronous thread dispatch."""
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.gates = {name: threading.BoundedSemaphore(size) for name, size in settings.pool_sizes.items()
                      if name != 'scheduler'}
        self.thread_capacity = sum(size for name, size in settings.pool_sizes.items() if name != 'scheduler') + 4
        self.waiters = deque()
        self.waiter_lock = threading.Lock()

    async def acquire(self, category):
        gate = self.gates[category]
        if category != 'control':
            return gate.acquire(blocking=False)
        # FIFO admission absorbs bursts without parking DB connections or
        # synchronous request threads. The short bounded wait is cancellable.
        ticket = object()
        with self.waiter_lock:
            if not self.waiters and gate.acquire(blocking=False):
                return True
            if len(self.waiters) >= 4 * self.settings.control:
                return False
            self.waiters.append(ticket)
        deadline = time.monotonic() + self.settings.pool_timeout
        try:
            while time.monotonic() < deadline:
                with self.waiter_lock:
                    if self.waiters[0] is ticket and gate.acquire(blocking=False):
                        self.waiters.popleft()
                        return True
                await asyncio.sleep(min(.005, max(0, deadline - time.monotonic())))
            return False
        finally:
            with self.waiter_lock:
                if ticket in self.waiters:
                    self.waiters.remove(ticket)

    async def __call__(self, scope, receive, send):
        # Liveness is a constant async response: no thread/DB consumption.
        if scope['type'] != 'http' or (scope.get('path') == '/healthz' and scope.get('method') == 'GET'):
            return await self.app(scope, receive, send)
        # Bound every HTTP request, including static/SPA routes, before sync
        # dispatch. Non-control admissions cannot consume the thread allowance
        # reserved for admitted control work. This does not isolate CPU or I/O.
        limiter = anyio.to_thread.current_default_thread_limiter()
        if limiter.total_tokens < self.thread_capacity:
            limiter.total_tokens = self.thread_capacity
        category = request_category(scope.get('path', ''), scope.get('method', 'GET'))
        gate = self.gates[category]
        started = time.monotonic()
        if not await self.acquire(category):
            LOG.warning('database admission rejected category=%s reason=concurrency', category)
            return await busy_response('request_concurrency')(scope, receive, send)
        scope.setdefault('state', {})['db_category'] = category
        token = request_budget.set(started + self.settings.request_timeout)
        try:
            await self.app(scope, receive, send)
        finally:
            request_budget.reset(token)
            gate.release()
            LOG.debug('database request finished category=%s elapsed_ms=%.1f', category,
                      (time.monotonic() - started) * 1000)
