"""Bounded online DB admission; scheduler and migrations do not use request deadlines."""
import asyncio
import anyio.to_thread
from collections import deque
from contextvars import ContextVar
from dataclasses import dataclass
import logging
import math
import os
import threading
import time

from sqlalchemy import create_engine, event
from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout
from sqlalchemy.orm import sessionmaker
from starlette.responses import JSONResponse
from .priority_routes import legacy_category_for

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
    route_group: str | None = None
    scheduler_enabled: bool = True

    def __post_init__(self):
        for value in (*self.pool_sizes.values(), self.processes, self.connection_budget):
            if type(value) is not int or value < 1:
                raise ValueError('database connection budgets must be positive integers')
        if self.route_group not in (None, 'ordinary', 'control'):
            raise ValueError('API route group must be ordinary or control')
        if self.route_group is not None and self.processes != 2:
            raise ValueError('split API requires exactly two processes')
        if self.route_group == 'control' and self.scheduler_enabled:
            raise ValueError('control API must not run the scheduler')
        # Both split processes receive the same configured category sizes. Each
        # creates only its own pools; sum the union once, not five pools twice.
        maximum = (sum(self.pool_sizes.values()) if self.route_group else
                   sum(self.pool_sizes.values()) * self.processes)
        if maximum > self.connection_budget:
            raise ValueError('API database pools exceed total connection budget')
        if not all(math.isfinite(v) and v > 0 for v in (self.pool_timeout, self.lock_timeout, self.request_timeout)):
            raise ValueError('database wait budgets must be finite and positive')
        if not self.pool_timeout < self.request_timeout < 5 or not self.lock_timeout < self.request_timeout:
            raise ValueError('database wait budgets must fit within Worker HTTP timeout (5 seconds)')

    @property
    def pool_sizes(self):
        return {name: getattr(self, name) for name in
                ('ordinary', 'control', 'observation', 'result', 'scheduler')}

    @property
    def active_pool_sizes(self):
        if self.route_group == 'control':
            return {'control': self.control}
        if self.route_group is None:
            # Preserve the existing single-process pool contract, including
            # tests and embedded deployments that invoke the scheduler.
            return self.pool_sizes
        names = ('ordinary', 'observation', 'result')
        active = {name: getattr(self, name) for name in names}
        if self.scheduler_enabled:
            active['scheduler'] = self.scheduler
        return active

    @classmethod
    def environment(cls):
        values = {key: cast(os.environ[name]) for key, name, cast in (
            ('ordinary', 'DB_POOL_SIZE', int), ('control', 'DB_CONTROL_POOL_SIZE', int),
            ('observation', 'DB_OBSERVATION_POOL_SIZE', int), ('result', 'DB_RESULT_POOL_SIZE', int),
            ('scheduler', 'DB_SCHEDULER_POOL_SIZE', int),
            ('processes', 'API_DB_PROCESS_COUNT', int), ('connection_budget', 'DB_CONNECTION_BUDGET', int),
            ('pool_timeout', 'DB_POOL_TIMEOUT_SECONDS', float), ('lock_timeout', 'DB_LOCK_TIMEOUT_SECONDS', float),
            ('request_timeout', 'DB_REQUEST_TIMEOUT_SECONDS', float),
        ) if name in os.environ}
        return cls(**values, route_group=os.getenv('API_ROUTE_GROUP') or None,
                   scheduler_enabled=os.getenv('SCHEDULER_ENABLED', '1') == '1')


def request_category(path, method='POST'):
    return legacy_category_for(path, method)


def control_path(path):
    return request_category(path) == 'control'


class RouteGroup:
    """Keep process routing independent from optional database backpressure."""
    def __init__(self, app, route_group):
        if route_group not in ('ordinary', 'control'):
            raise ValueError('API route group must be ordinary or control')
        self.app, self.route_group = app, route_group

    async def __call__(self, scope, receive, send):
        if scope['type'] == 'http' and not (scope.get('path') == '/healthz' and scope.get('method') == 'GET'):
            category = request_category(scope.get('path', ''), scope.get('method', 'GET'))
            if (category == 'control') != (self.route_group == 'control'):
                return await JSONResponse({'detail': 'API route group mismatch'}, status_code=404)(scope, receive, send)
        return await self.app(scope, receive, send)


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
    if settings.route_group is not None and not isolated:
        raise ValueError('split API requires isolated role-specific pools')
    if url.startswith('sqlite:'):
        factory = sessionmaker(create_engine(url), expire_on_commit=False)
        return ({name: factory for name in settings.active_pool_sizes} if isolated else (factory, factory))
    def make(size, category=None):
        wait = settings.pool_seconds(category) if hasattr(settings, 'pool_seconds') else settings.pool_timeout
        engine = create_engine(url, pool_size=size, max_overflow=0, pool_timeout=wait,
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
        return {name: make(size, name) for name, size in settings.active_pool_sizes.items()}
    return make(settings.ordinary), make(settings.control)


class Admission:
    """Reject before authentication, DB checkout and synchronous thread dispatch."""
    def __init__(self, app, settings, route_group=None, admission_enabled=True):
        self.app, self.settings = app, settings
        if route_group not in (None, 'ordinary', 'control'):
            raise ValueError('API route group must be ordinary or control')
        self.route_group = route_group
        self.admission_enabled = admission_enabled
        self.gates = {name: threading.BoundedSemaphore(size) for name, size in settings.active_pool_sizes.items()
                      if name != 'scheduler'}
        # A split process has the same 40-token default as its DB allowance;
        # do not silently enlarge the synchronous thread pool beyond it.
        self.thread_capacity = (sum(settings.active_pool_sizes.values()) if settings.route_group else
                                sum(size for name, size in settings.active_pool_sizes.items() if name != 'scheduler') + 4)
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
        category = request_category(scope.get('path', ''), scope.get('method', 'GET'))
        if self.route_group is not None and (category == 'control') != (self.route_group == 'control'):
            return await JSONResponse({'detail': 'API route group mismatch'}, status_code=404)(scope, receive, send)
        if category not in self.gates:
            return await JSONResponse({'detail': 'API route group mismatch'}, status_code=404)(scope, receive, send)
        # Bound every HTTP request, including static/SPA routes, before sync
        # dispatch. Non-control admissions cannot consume the thread allowance
        # reserved for admitted control work. This does not isolate CPU or I/O.
        limiter = anyio.to_thread.current_default_thread_limiter()
        if limiter.total_tokens < self.thread_capacity:
            limiter.total_tokens = self.thread_capacity
        gate = self.gates[category]
        started = time.monotonic()
        if self.admission_enabled and not await self.acquire(category):
            LOG.warning('database admission rejected category=%s reason=concurrency', category)
            return await busy_response('request_concurrency')(scope, receive, send)
        scope.setdefault('state', {})['db_category'] = category
        token = request_budget.set(started + self.settings.request_timeout)
        try:
            await self.app(scope, receive, send)
        finally:
            request_budget.reset(token)
            if self.admission_enabled:
                gate.release()
            LOG.debug('database request finished category=%s elapsed_ms=%.1f', category,
                      (time.monotonic() - started) * 1000)
