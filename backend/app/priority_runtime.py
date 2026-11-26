"""Opt-in four-role capacity; legacy single/dual process settings stay separate."""
import asyncio
from collections import deque
from dataclasses import dataclass, field
import hashlib
import json
import math
import os
import time
from urllib.parse import urlsplit

import anyio.to_thread
import httpx
from sqlalchemy import text
from starlette.responses import JSONResponse

from . import db_budget
from .priority_routes import ROLES, accepts_role, category_for


ROLE_POOLS = {
    'execution-control': ('execution_control',),
    'interaction': ('user_action', 'user_long', 'page_read'),
    'worker-data': ('worker_claim', 'worker_result', 'worker_observation', 'scheduler'),
    'bulk-transfer': ('bulk_transfer',),
}
DEFAULT_POOLS = dict(execution_control=24, user_action=10, user_long=4, page_read=10,
                     worker_claim=6, worker_result=7, worker_observation=8, scheduler=1, bulk_transfer=10)
SCALED_POOLS = dict(DEFAULT_POOLS, execution_control=12)
PEERS = {role: f'http://{role}-api:8000' for role in ROLE_POOLS}


@dataclass(frozen=True)
class Settings:
    route_group: str
    scheduler_enabled: bool
    pool_sizes: dict = field(default_factory=lambda: DEFAULT_POOLS.copy())
    processes: int = 4
    control_replicas: int = 1
    interaction_replicas: int = 1
    connection_budget: int = 80
    connection_headroom: int = 20
    pool_timeout: float = .25
    lock_timeout: float = .5
    request_timeout: float = 4
    long_request_timeout: float = 30
    peers: dict = field(default_factory=lambda: PEERS.copy())
    admission_limits: dict = field(default_factory=dict)
    queue_limits: dict = field(default_factory=dict)
    queue_waits: dict = field(default_factory=dict)
    pool_waits: dict = field(default_factory=dict)
    request_budgets: dict = field(default_factory=dict)

    def __post_init__(self):
        if (self.route_group not in ROLE_POOLS or type(self.control_replicas) is not int or
                self.control_replicas not in (1, 2) or type(self.processes) is not int or
                type(self.interaction_replicas) is not int or not 1 <= self.interaction_replicas <= 8
                or self.processes != 2 + self.control_replicas + self.interaction_replicas):
            raise ValueError('priority API requires four roles and a matching process count')
        if self.scheduler_enabled != (self.route_group == 'worker-data'):
            raise ValueError('only worker-data must run the priority scheduler')
        if not isinstance(self.pool_sizes, dict) or set(self.pool_sizes) != set(DEFAULT_POOLS):
            raise ValueError('priority pool manifest must include every category exactly once')
        if any(type(value) is not int or value < 1 for value in
               (*self.pool_sizes.values(), self.connection_budget, self.connection_headroom)):
            raise ValueError('priority connection budgets must be positive integers')
        if self.configured_connections > self.connection_budget:
            raise ValueError('priority API pools exceed total connection budget')
        if self.pool_sizes['scheduler'] != 1:
            raise ValueError('priority scheduler pool must have one connection')
        waits = (self.pool_timeout, self.lock_timeout, self.request_timeout, self.long_request_timeout)
        if not all(math.isfinite(v) and v > 0 for v in waits):
            raise ValueError('priority wait budgets must be finite and positive')
        if not self.pool_timeout < self.request_timeout < 5 or not self.lock_timeout < self.request_timeout:
            raise ValueError('priority short requests must fit the Worker five-second timeout')
        if not 20 < self.long_request_timeout <= 60:
            raise ValueError('priority long request budget must cover SSH waits and be at most 60 seconds')
        http_categories = set(DEFAULT_POOLS) - {'scheduler'}
        for name in ('admission_limits', 'queue_limits', 'queue_waits', 'pool_waits', 'request_budgets'):
            values = getattr(self, name)
            allowed = set(DEFAULT_POOLS) if name == 'pool_waits' else http_categories
            if not isinstance(values, dict) or set(values) - allowed:
                raise ValueError('invalid priority capacity categories: ' + name)
            for value in values.values():
                if name in ('admission_limits', 'queue_limits'):
                    valid = type(value) is int and value >= (0 if name == 'queue_limits' else 1)
                else:
                    valid = type(value) in (int, float) and math.isfinite(value) and value >= 0
                    if name != 'queue_waits':
                        valid = valid and value > 0
                if not valid:
                    raise ValueError('invalid priority capacity value: ' + name)
        for category in http_categories:
            budget = self.request_seconds(category)
            maximum = 60 if category in ROLE_POOLS['interaction'] else 5
            if (budget > maximum or (maximum == 5 and budget == 5)
                    or (category in (self.queue_waits.keys() | self.pool_waits.keys() | self.request_budgets.keys())
                        and self.queue_seconds(category) + self.pool_seconds(category) >= budget)
                    or self.lock_timeout >= budget):
                raise ValueError('priority category waits must fit request and client budgets: ' + category)
        if not isinstance(self.peers, dict) or set(self.peers) != set(ROLE_POOLS):
            raise ValueError('priority health peers must include every role')
        for address in self.peers.values():
            parsed = urlsplit(address)
            if (parsed.scheme not in ('http', 'https') or not parsed.netloc or parsed.username or parsed.password
                    or parsed.path or parsed.query or parsed.fragment):
                raise ValueError('priority health peers must be HTTP origins without credentials')

    @property
    def active_pool_sizes(self):
        return {name: self.pool_sizes[name] for name in ROLE_POOLS[self.route_group]}

    def admission_size(self, category):
        return self.admission_limits.get(category, self.pool_sizes[category])

    def queue_size(self, category):
        return self.queue_limits.get(category, 4 * self.admission_size(category))

    def queue_seconds(self, category):
        return self.queue_waits.get(category, self.pool_timeout)

    def pool_seconds(self, category):
        return self.pool_waits.get(category, self.pool_timeout)

    def request_seconds(self, category):
        default = self.long_request_timeout if category == 'user_long' else self.request_timeout
        return self.request_budgets.get(category, default)

    @property
    def configured_connections(self):
        return (sum(self.pool_sizes.values()) + (self.control_replicas - 1) * self.pool_sizes['execution_control']
                + (self.interaction_replicas - 1) * sum(self.pool_sizes[k] for k in ROLE_POOLS['interaction']))

    @property
    def fingerprint(self):
        values = {name: value for name, value in self.__dict__.items()
                  if name not in ('route_group', 'scheduler_enabled')}
        return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()[:16]

    @classmethod
    def environment(cls):
        values = {key: cast(os.environ[name]) for key, name, cast in (
            ('pool_sizes', 'PRIORITY_API_POOL_SIZES', json.loads),
            ('admission_limits', 'PRIORITY_API_ADMISSION_LIMITS', json.loads),
            ('queue_limits', 'PRIORITY_API_QUEUE_LIMITS', json.loads),
            ('queue_waits', 'PRIORITY_API_QUEUE_WAITS', json.loads),
            ('pool_waits', 'PRIORITY_API_POOL_WAITS', json.loads),
            ('request_budgets', 'PRIORITY_API_REQUEST_BUDGETS', json.loads),
            ('peers', 'PRIORITY_API_PEERS', json.loads),
            ('processes', 'API_DB_PROCESS_COUNT', int), ('interaction_replicas', 'API_INTERACTION_REPLICAS', int), ('control_replicas', 'API_CONTROL_REPLICAS', int),
            ('connection_budget', 'DB_CONNECTION_BUDGET', int),
            ('connection_headroom', 'DB_CONNECTION_HEADROOM', int),
            ('pool_timeout', 'DB_POOL_TIMEOUT_SECONDS', float), ('lock_timeout', 'DB_LOCK_TIMEOUT_SECONDS', float),
            ('request_timeout', 'DB_REQUEST_TIMEOUT_SECONDS', float),
            ('long_request_timeout', 'DB_LONG_REQUEST_TIMEOUT_SECONDS', float),
        ) if name in os.environ and os.environ[name]}
        if 'pool_sizes' not in values and values.get('control_replicas', 1) == 2:
            values['pool_sizes'] = SCALED_POOLS.copy()
        return cls(route_group=os.environ['API_ROUTE_GROUP'],
                   scheduler_enabled=os.getenv('SCHEDULER_ENABLED', '1') == '1', **values)


def check_database_budget(factory, settings):
    """Startup only: reserve configured headroom beyond all four role pools."""
    with factory() as session:
        if session.bind.dialect.name != 'postgresql':
            raise ValueError('priority API deployment requires PostgreSQL')
        maximum = int(session.scalar(text('SHOW max_connections')))
        reserved = int(session.scalar(text('SHOW superuser_reserved_connections')))
        if settings.connection_headroom < reserved or settings.connection_budget + settings.connection_headroom > maximum:
            raise ValueError('priority connection budget and headroom exceed PostgreSQL capacity')


class Admission:
    """Per-category cancellable queues before auth, sync threads or DB checkout."""
    def __init__(self, app, settings):
        self.app, self.settings = app, settings
        self.available = {name: settings.admission_size(name) for name in settings.active_pool_sizes if name != 'scheduler'}
        self.waiters = {name: deque() for name in self.available}
        self.thread_capacity = max(40, sum(self.available.values()) + 4)

    async def acquire(self, category):
        queue = self.waiters[category]
        if not queue and self.available[category]:
            self.available[category] -= 1
            return True
        if len(queue) >= self.settings.queue_size(category):
            return False
        ticket = object()
        queue.append(ticket)
        deadline = time.monotonic() + self.settings.queue_seconds(category)
        try:
            while time.monotonic() < deadline:
                if queue[0] is ticket and self.available[category]:
                    queue.popleft()
                    self.available[category] -= 1
                    return True
                await asyncio.sleep(min(.005, max(0, deadline - time.monotonic())))
            return False
        finally:
            if ticket in queue:
                queue.remove(ticket)

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        path, method = scope.get('path', ''), scope.get('method', 'GET')
        if not accepts_role(self.settings.route_group, path, method):
            return await JSONResponse({'detail': 'API route group mismatch'}, status_code=404)(scope, receive, send)
        if path == '/healthz' and method == 'GET':
            return await self.app(scope, receive, send)
        category = category_for(path, method)
        if category == 'health':
            category = next(iter(self.available))
        elif category == 'fallback':
            category = 'page_read'
        limiter = anyio.to_thread.current_default_thread_limiter()
        limiter.total_tokens = max(limiter.total_tokens, self.thread_capacity)
        started = time.monotonic()
        if not await self.acquire(category):
            db_budget.LOG.warning('database admission rejected category=%s reason=concurrency wait_ms=%.3f',
                                  category, (time.monotonic() - started) * 1000)
            return await db_budget.busy_response('request_concurrency')(scope, receive, send)
        db_budget.LOG.debug('database admission accepted category=%s wait_ms=%.3f queued=%s',
                            category, (time.monotonic() - started) * 1000, len(self.waiters[category]))
        scope.setdefault('state', {})['db_category'] = category
        seconds = self.settings.request_seconds(category)
        token = db_budget.request_budget.set(started + seconds)
        try:
            await self.app(scope, receive, send)
        finally:
            db_budget.request_budget.reset(token)
            self.available[category] += 1
            db_budget.LOG.debug('database request finished category=%s elapsed_ms=%.1f', category,
                                (time.monotonic()-started)*1000)


async def deployment_health(settings, *, peers=None, transport=None):
    """No DB or sync threads; each peer reports only its own liveness/config."""
    async with httpx.AsyncClient(timeout=1, trust_env=False, transport=transport) as client:
        async def check(role, base):
            try:
                reply = await client.get(base + '/healthz')
                payload = reply.json()
                return (reply.status_code == 200 and isinstance(payload, dict)
                        and payload.get('role') == role and payload.get('budget') == settings.fingerprint)
            except (httpx.HTTPError, ValueError):
                return False
        targets = settings.peers if peers is None else peers
        results = await asyncio.gather(*(check(role, base) for role, base in targets.items()))
    states = dict(zip(targets, results))
    healthy = set(states) == set(ROLE_POOLS) and all(states.values())
    return JSONResponse({'status': 'ok' if healthy else 'degraded', 'roles': states}, status_code=200 if healthy else 503)
