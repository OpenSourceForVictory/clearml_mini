"""Disposable real HTTP/PostgreSQL control-plane load. Never targets an existing database."""
import argparse
import asyncio
from collections import Counter, defaultdict, deque
from contextlib import AsyncExitStack, ExitStack
from datetime import timedelta
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import re
import resource
import socket
import subprocess
import sys
import time
import uuid

import httpx
import jwt
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from worker.backpressure import parse_retry_after
from backend.app.core import Base, User, Pool, Worker, AgentObservation, Slot, Job, Run, ServiceInstance, ClientTemplate, ExecutionRecovery, ExecutionRecoveryControl, now
from backend.app import priority_runtime, priority_gateway
from backend.tests.control_plane_metrics import (observe, with_outage, expired_leases_during_load,
                                                 peak_execution_overlap, full_execution_overlap_seconds)


def seed(engine, count, *, agent_telemetry=False, mixed_ui=False,
         recovery_load=False, cards_per_node=1, command_workers=1):
    if type(cards_per_node) is not int or cards_per_node < 1:
        raise ValueError('cards_per_node must be a positive integer')
    Base.metadata.create_all(engine)
    with sessionmaker(engine).begin() as s:
        s.add(User(id='u', username='load-test', password_hash='unused'))
        s.add(Pool(id='p', name='load-test', gpu_model='virtual'))
        if mixed_ui:
            s.add(ClientTemplate(id='load-template', name='load-command', version='1', image='load:command',
                                 command=['true'], protocols=[], task_type='command',
                                 integration_mode='sdk-task-result', status='published'))
    identities = []
    recovery_grants = {}
    nodes, node_groups = {}, {}
    for i in range(count):
        identity, token = uuid.uuid4().hex, 'wk_' + uuid.uuid4().hex
        agent_session = uuid.uuid4().hex
        node_index = i//cards_per_node
        first_card = node_index not in nodes
        if first_card:
            nodes[node_index] = {'worker_id':identity, 'token':token,
                                 'session':agent_session, 'slots':[]}
        node = nodes[node_index]
        token, agent_session = node['token'], node['session']
        node['slots'].append(identity)
        node_groups[identity] = node
        with sessionmaker(engine).begin() as s:
            connection = {'agent': {'session': agent_session, 'boot_id': uuid.uuid4().hex,
                                    'resource_protocol': 1, 'revision': 0,
                                    'desired': {'heartbeat_seconds': 5, 'sample_seconds': 15, 'programs': 'running'}}} if agent_telemetry else {}
            if first_card:
                s.add(Worker(id=node['worker_id'], node_name='load-'+str(node_index),
                             token_hash=hashlib.sha256(token.encode()).hexdigest(),
                             max_concurrent=cards_per_node, connection=connection))
                s.flush()
                if agent_telemetry:
                    s.add(AgentObservation(worker_id=node['worker_id'], session=agent_session, payload={}))
            s.add(Slot(id=identity, worker_id=node['worker_id'], pool_id='p', node_name='load-'+str(node_index), queue_name='load-'+identity, gpu_index=i%cards_per_node,
                       gpu_uuid='mock:'+identity, gpu_model='virtual', vram_mb=1024,
                       protocol_version=18 if mixed_ui and i >= count-command_workers else 1,
                       client_images=['load:command']))
            s.flush()
            if mixed_ui and i >= count-command_workers:
                identities.append((identity, token, agent_session) if agent_telemetry else (identity, token))
                continue
            s.add(ServiceInstance(id=identity, name='load', worker_id=node['worker_id'], slot_id=identity,
                                  address='127.0.0.1', port=19000, template_snapshot={}, current_job_id=identity))
            s.flush()
            s.add(Job(id=identity, owner_id='u', pool_id='p', title='load', benchmark='mock', model_ref='mock',
                      episodes=1, status='RUNNING', service_instance_id=identity,
                      client_spec=({'template':{'task_type':'command',
                                                'integration_mode':'sdk-task-result'},
                                    'timeout_seconds':0} if recovery_load else {})))
            s.flush()
            s.add(Run(id=identity, job_id=identity, slot_id=identity, execution_id=identity, attempt=1,
                      status='STARTING' if recovery_load else 'RUNNING',
                      cleanup_confirmed=not recovery_load,
                      enqueued_at=now() if recovery_load else None,
                      lease_expires_at=now()+timedelta(seconds=30)))
            s.flush()
            if recovery_load:
                from backend.app.recovery_claims import bind
                receipt = bind(s, request_id=str(uuid.uuid4()), worker_id=node['worker_id'],
                               slot_id=identity, run_id=identity,
                               task={'execution_id':identity,'run_id':identity,'job_id':identity,
                                     'benchmark':'mock','episodes':1}, tolerance=None)
                recovery_grants[identity] = receipt
        identities.append((identity, token, agent_session) if agent_telemetry else (identity, token))
    return (identities, recovery_grants, node_groups) if recovery_load else identities


def percentile(values, fraction):
    return sorted(values)[min(len(values)-1, int(len(values)*fraction))] if values else None


def capacity_waits(logs):
    """Summarize safe category/timing fields only; ignore URLs, SQL and request bodies."""
    pool_waits, admission_waits = {}, {}
    for log in logs:
        for category, outcome, wait in re.findall(
                r'database pool checkout category=(\w+) outcome=(ok|timeout) wait_ms=([0-9.]+)', log):
            pool_waits.setdefault(category, {'ok': [], 'timeout': []})[outcome].append(float(wait))
        for outcome, category, wait in re.findall(
                r'database admission (accepted|rejected) category=(\w+).*?wait_ms=([0-9.]+)', log):
            admission_waits.setdefault(category, {'accepted': [], 'rejected': []})[outcome].append(float(wait))
    return ({category: {'count': len(values['ok']), 'timeout_count': len(values['timeout']),
                        'p95': percentile(values['ok'], .95), 'p99': percentile(values['ok'], .99)}
             for category, values in pool_waits.items()},
            {category: {'count': len(values['accepted']), 'rejected_count': len(values['rejected']),
                        'p95': percentile(values['accepted'], .95), 'p99': percentile(values['accepted'], .99)}
             for category, values in admission_waits.items()})


def recovery_database_counts(connection):
    rows = connection.execute(select(Run.status, Run.cleanup_confirmed,
                                     ExecutionRecoveryControl.snapshot)
        .join(ExecutionRecovery, ExecutionRecovery.run_id == Run.id)
        .outerjoin(ExecutionRecoveryControl,
                   ExecutionRecoveryControl.execution_id == ExecutionRecovery.execution_id)).all()
    return {'completed':sum(status == 'SUCCEEDED' for status, _, _ in rows),
            'cleanup_confirmed':sum(bool(cleanup) for _, cleanup, _ in rows),
            'result_sync_complete':sum(bool(snapshot and snapshot.get('result_sync') == 'complete')
                                        for _, _, snapshot in rows)}


def render_gateway_config(template, ordinary_port, control_port, gateway_port):
    markers = ('listen 8000;', 'set $control_backend control-api:8000;',
               'set $ordinary_backend api:8000;')
    if any(marker not in template for marker in markers):
        raise ValueError('split API gateway template is missing a required route marker')
    return (template.replace('listen 8000;', f'listen 127.0.0.1:{gateway_port};')
            .replace('set $control_backend control-api:8000;',
                     f'set $control_backend 127.0.0.1:{control_port};')
            .replace('set $ordinary_backend api:8000;',
                     f'set $ordinary_backend 127.0.0.1:{ordinary_port};'))


def render_priority_gateway_config(ports, gateway_port, control_extra_port=None,
                                   interaction_extra_ports=(), bulk_extra_port=None):
    if set(ports) != set(priority_runtime.ROLE_POOLS):
        raise ValueError('priority gateway requires every role port')
    config = priority_gateway.render().replace('listen 8000;', f'listen 127.0.0.1:{gateway_port};')
    for role, port in ports.items():
        config = config.replace(priority_gateway.BACKENDS[role], f'127.0.0.1:{port}')
    if control_extra_port is not None:
        control_address = f'127.0.0.1:{ports["execution-control"]}'
        config = config.replace(control_address, 'execution-control-load-pool')
        config = (f'upstream execution-control-load-pool {{ server {control_address}; '
                  f'server 127.0.0.1:{control_extra_port}; }}\n' + config)
    if interaction_extra_ports:
        address = f'127.0.0.1:{ports["interaction"]}'
        config = config.replace(address, 'interaction-load-pool')
        servers = ' '.join(f'server 127.0.0.1:{p};' for p in interaction_extra_ports)
        config = f'upstream interaction-load-pool {{ server {address}; {servers} }}\n' + config
    if bulk_extra_port is not None:
        address = f'127.0.0.1:{ports["bulk-transfer"]}'
        config = config.replace(address, 'bulk-transfer-load-pool')
        config = (f'upstream bulk-transfer-load-pool {{ server {address}; '
                  f'server 127.0.0.1:{bulk_extra_port}; }}\n' + config)
    return config


def api_log_names(count, split_api, priority_api=False):
    if priority_api:
        return [f'api-{role}-{count}.log' for role in (*priority_runtime.ROLE_POOLS, 'execution-control-extra')]
    return ([f'api-ordinary-{count}.log', f'api-control-{count}.log'] if split_api
            else [f'api-{count}.log'])


def pool_profiles(ordinary, control, observation, *, split_api=False, role_aware=False,
                  scheduler_enabled=True, result=None):
    if role_aware and not split_api:
        raise ValueError('role-aware pools require split API')
    common = {'ordinary': ordinary, 'control': control, 'observation': observation,
              'result': result if result is not None else (3 if role_aware else 2)}
    if scheduler_enabled or not role_aware:
        common['scheduler'] = 1
    if split_api and role_aware:
        return {'ordinary': {name: size for name, size in common.items() if name != 'control'},
                'control': {'control': control}}
    return {'ordinary': common, **({'control': common.copy()} if split_api else {})}


def log_payload(identity, sequence, size, varied=False):
    if not varied:
        return 'x' * size
    # Deterministic retries but distinct lines/chunks; avoid an all-x TOAST bias.
    rows = []
    for line in range(size // 128 + 1):
        detail = hashlib.shake_256(f'{identity}:{sequence}:{line}'.encode()).hexdigest(64)
        rows.append(f'INFO worker={identity} chunk={sequence} step={line} event=simulation_progress detail={detail}\n')
    return ''.join(rows)[:size]


def acceptable_responses(by_kind, *, mixed_ui, bulk_outage, control_outage=False):
    for kind, counts in by_kind.items():
        allowed = {'200', '503'} | ({'201'} if mixed_ui else set())
        if bulk_outage and kind in ('logs', 'artifact'):
            allowed |= {'502', '504'}
        if control_outage and kind in ('recovery_control', 'recovery_exit', 'recovery_complete',
                                       'recovery_cleanup', 'recovery_result', 'recovery_results_finished',
                                       'claim', 'health', 'heartbeat'):
            allowed |= {'502', '504'}
        if set(counts) - allowed:
            return False
    return True


async def exercise(base, identities, seconds, log_bytes, *, independent_logs=False, drain_seconds=0,
                   trace_http_phases=False, client_per_worker=False, agent_telemetry=False,
                   progress_path=None, log_interval=2, user_token=None, continuous_batches=False,
                   varied_logs=False, recovery_grants=None, node_groups=None,
                   command_workers=1, batch_cases=1, web_tokens=(), idle_claim_seconds=.5,
                   command_hold_seconds=0):
    latency, codes, reasons = defaultdict(list), Counter(), Counter()
    successful_latency = defaultdict(list)
    by_kind, busy_by_kind = defaultdict(Counter), defaultdict(Counter)
    web_transport_errors, web_transport_error_windows = Counter(), Counter()
    latest_heartbeat, latest_sample, latest_agent = {}, {}, {}
    worst_heartbeat_age = 0
    log_queues = {item[0]: deque() for item in identities}
    log_stats = Counter()
    max_log_pending = max_log_age = 0
    loop_lag = []
    http_phases = defaultdict(list)
    progress = []
    ui_evidence = {'batch_id': None, 'control_revisions': [], 'last_batch_status': None,
                   'retried': False, 'retry_count':0, 'batch_count':0, 'completed_batches':[],
                   'executions': [], 'duplicate_results': 0, 'artifact_seen': False,
                   'case_count_seen': 0, 'max_pending_count': 0, 'errors': []}
    running_identities = identities[:-command_workers] if user_token else identities
    recovery_grants = recovery_grants or {}
    node_groups = node_groups or {}
    lifecycle_ids = set(item[0] for item in running_identities[:max(1, len(running_identities)//10)]) if recovery_grants else set()
    recovered = set()
    optional_report_locks = {item[0]: asyncio.Lock() for item in running_identities}
    recovery_evidence = {'target_lifecycle':len(lifecycle_ids), 'completed':0,
                         'cleanup_confirmed':0, 'result_sync_complete':0,
                         'duplicate_results':0, 'errors':[]}
    release_latencies = []
    started = time.monotonic()
    async with AsyncExitStack() as stack:
        if client_per_worker:
            clients = {identity: await stack.enter_async_context(httpx.AsyncClient(
                base_url=base, trust_env=False, timeout=5,
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=4)))
                for identity, *_ in identities}
        else:
            shared = await stack.enter_async_context(httpx.AsyncClient(
                base_url=base, trust_env=False, timeout=5,
                limits=httpx.Limits(max_connections=len(identities)*4,
                                    max_keepalive_connections=len(identities)*4)))
            clients = {identity: shared for identity, *_ in identities}
        ui_client = await stack.enter_async_context(httpx.AsyncClient(
            base_url=base, trust_env=False, timeout=5,
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4))) if user_token else None
        async def post(kind, identity, token, path, body):
            begin = time.monotonic()
            events = {}
            async def trace(event, info):
                if event in ('connection.connect_tcp.started', 'connection.connect_tcp.complete',
                             'http11.send_request_headers.started',
                             'http11.receive_response_headers.started',
                             'http11.receive_response_headers.complete'):
                    events[event] = time.monotonic()
            try:
                reply = await clients[identity].post('/api/v1/'+path, json=body, headers={'Authorization': 'Bearer '+token},
                                       extensions={'trace': trace} if trace_http_phases else None)
                codes[str(reply.status_code)] += 1
                by_kind[kind][str(reply.status_code)] += 1
                if reply.status_code == 200:
                    successful_latency[kind].append(time.monotonic()-begin)
                if reply.status_code == 503:
                    reason = reply.json().get('reason', 'unspecified_busy')
                    reasons[reason] += 1
                    busy_by_kind[kind][reason] += 1
                return reply
            except httpx.TransportError:
                codes['transport_error'] += 1
                by_kind[kind]['transport_error'] += 1
                return None
            finally:
                latency[kind].append(time.monotonic()-begin)
                if trace_http_phases:
                    first = min((value for name, value in events.items() if name.endswith('.started')), default=None)
                    if first is not None:
                        http_phases['client_before_transport'].append(first - begin)
                    for key, start, finish in (
                        ('tcp_connect', 'connection.connect_tcp.started', 'connection.connect_tcp.complete'),
                        ('response_headers_wait', 'http11.receive_response_headers.started',
                         'http11.receive_response_headers.complete')):
                        if start in events and finish in events:
                            http_phases[key].append(events[finish] - events[start])
        async def sample_loop_lag():
            target = time.monotonic() + .05
            while time.monotonic() - started < seconds:
                await asyncio.sleep(max(0, target - time.monotonic()))
                current = time.monotonic()
                loop_lag.append(max(0, current - target))
                target = current + .05
        async def sample_progress():
            while time.monotonic() - started < seconds:
                await asyncio.sleep(min(30, max(0, started + seconds - time.monotonic())))
                observed = time.monotonic()
                pending = [(created, identity) for identity, queue in log_queues.items()
                           for created, _ in queue]
                row = {'elapsed_seconds': round(observed - started, 2),
                       'max_current_heartbeat_age_seconds': max((observed-v for v in latest_heartbeat.values()), default=None),
                       'worst_heartbeat_gap_seconds': worst_heartbeat_age,
                       'log_generated': log_stats['generated'], 'log_accepted': log_stats['accepted'],
                       'log_pending': len(pending),
                       'oldest_pending_log_seconds': max((observed-created for created, _ in pending), default=0),
                       'heartbeat_admission_rejections': busy_by_kind['heartbeat']['request_concurrency'],
                       'log_admission_rejections': busy_by_kind['logs']['request_concurrency'],
                       'agent_admission_rejections': busy_by_kind['agent']['request_concurrency']}
                progress.append(row)
                if progress_path is not None:
                    with progress_path.open('a') as stream:
                        stream.write(json.dumps(row)+'\n')
        async def produce_logs(identity):
            nonlocal max_log_pending
            queue = log_queues[identity]
            gap_needed = False
            await asyncio.sleep(random.uniform(0, 1))
            while time.monotonic() - started < seconds:
                log_stats['generated'] += 1
                if len(queue) >= 256:
                    log_stats['dropped'] += 1
                    gap_needed = True
                else:
                    queue.append((time.monotonic(), gap_needed))
                    gap_needed = False
                    max_log_pending = max(max_log_pending, len(queue))
                await asyncio.sleep(log_interval)
            if gap_needed:
                log_stats['unreported_gap_workers'] += 1

        async def send_logs(identity, token):
            nonlocal max_log_age
            queue = log_queues[identity]
            sequence, retry = 0, .25
            deadline = started + seconds + drain_seconds
            path = f'worker/executions/{identity}/logs-v2'
            while time.monotonic() < deadline:
                if not queue:
                    await asyncio.sleep(.02)
                    continue
                created, gap = queue[0]
                max_log_age = max(max_log_age, time.monotonic() - created)
                reply = await post('logs', identity, token, path,
                                   {'sequence': sequence, 'content': log_payload(identity, sequence, log_bytes, varied_logs), 'gap': gap})
                if reply is not None and reply.status_code == 200:
                    if reply.json().get('next_sequence') != sequence + 1:
                        log_stats['unexpected_ack'] += 1
                        break
                    queue.popleft()
                    sequence += 1
                    log_stats['accepted'] += 1
                    retry = .25
                elif reply is not None and reply.status_code not in (503, 429, 502, 504):
                    log_stats['permanent_error'] += 1
                    break
                else:
                    hint = parse_retry_after(reply.headers.get('Retry-After')) if reply is not None else None
                    await asyncio.sleep((hint or 0) + random.uniform(retry / 2, retry))
                    retry = min(2, retry * 2)

        async def agent(identity, token, session):
            sequence, retry = 1, .25
            latest_agent[identity] = started
            await asyncio.sleep(random.uniform(0, 1))
            while time.monotonic() - started < seconds:
                sampled = time.time()
                cards = node_groups[identity]['slots'] if identity in node_groups else [identity]
                gpu_values = {key:value for index, slot in enumerate(cards) for key,value in (
                    (f'gpu_{index}_uuid', 'mock:'+slot),
                    (f'gpu_{index}_total_mb', 1024),
                    (f'gpu_{index}_free_mb', 1024),
                    (f'gpu_{index}_utilization', 0))}
                reply = await post('agent', identity, token, 'agent/heartbeat', {
                    'session': session, 'sequence': sequence, 'applied_revision': 0,
                    'config_status': 'applied',
                    'samples': {'gpu': {'status': 'ok', 'sampled_at': sampled,
                                        'values': gpu_values},
                                'host': {'status': 'ok', 'sampled_at': sampled,
                                         'values': {'cpu_count': 32, 'memory_total_mb': 65536,
                                                    'memory_available_mb': 65536}}}})
                if reply is not None and reply.status_code == 200:
                    latest_agent[identity] = time.monotonic()
                    sequence += 1
                    retry = .25
                    await asyncio.sleep(5)
                else:
                    hint = parse_retry_after(reply.headers.get('Retry-After')) if reply is not None else None
                    await asyncio.sleep((hint or 0) + random.uniform(retry / 2, retry))
                    retry = min(2, retry * 2)

        async def worker(identity, token):
            nonlocal worst_heartbeat_age
            seq = 0
            retry = .25
            latest_heartbeat[identity] = latest_sample[identity] = started
            await asyncio.sleep(random.uniform(0, 1))
            while time.monotonic()-started < seconds:
                cycle = time.monotonic()
                reply = await post('heartbeat', identity, token, 'worker/heartbeat', {'slot_id':identity,
                                    **({} if recovery_grants else {'execution_id':identity}),
                                    'telemetry': {'free_vram_mb':1024, 'gpu_utilization':0}})
                age = time.monotonic()-latest_heartbeat[identity]
                worst_heartbeat_age = max(worst_heartbeat_age, age)
                if reply is not None and reply.status_code == 200:
                    latest_heartbeat[identity] = latest_sample[identity] = time.monotonic()
                    retry = .25
                else:
                    # Match Worker: after a missed renewal return to heartbeat,
                    # not another whole cycle of optional reports/claims.
                    hint = parse_retry_after(reply.headers.get('Retry-After')) if reply is not None else None
                    await asyncio.sleep((hint or 0) + random.uniform(retry / 2, retry))
                    retry = min(2, retry * 2)
                    continue
                # A terminal transition must not overtake an optional upload
                # already in flight for this simulated execution.
                async with optional_report_locks[identity]:
                    if identity not in recovered:
                        await post('health', identity, token, f'worker/executions/{identity}/service-health', {'ready':True})
                        if not independent_logs:
                            reply = await post('logs', identity, token, f'worker/executions/{identity}/logs-v2',
                                               {'sequence':seq, 'content':log_payload(identity, seq, log_bytes, varied_logs)})
                            if reply is not None and reply.status_code == 200:
                                seq = reply.json()['next_sequence']
                        await post('artifact', identity, token, f'worker/executions/{identity}/artifacts', {'name':'load.json','content_base64':'e30='})
                        if recovery_grants:
                            receipt = recovery_grants[identity]
                            await post('claim', identity, token, f'worker/slots/{identity}/recovery/claim',
                                       {'request_id':receipt['request_id']})
                        else:
                            await post('claim', identity, token, f'worker/slots/{identity}/claim', {'known_execution_ids':[identity]})
                await asyncio.sleep(max(.05, 2-(time.monotonic()-cycle)) + random.uniform(0,.1))

        async def recovery_control(identity, token):
            receipt = recovery_grants[identity]
            generation = receipt['generation']
            bound = {'kind':'host', 'boot_id':'simulated-load-boot',
                     'runtime_id':hashlib.md5(identity.encode()).hexdigest()}
            path = f'worker/executions/{identity}'
            sequence = 1
            deadline = started + seconds + max(10, drain_seconds)
            finish_at = started + seconds*.6 if identity in lifecycle_ids else float('inf')

            async def reliable(kind, suffix, body):
                retry = .25
                while time.monotonic() < deadline:
                    reply = await post(kind, identity, token, path+suffix, body)
                    if reply is not None and reply.status_code == 200:
                        return reply.json()
                    if reply is not None and reply.status_code not in (429, 502, 503, 504):
                        recovery_evidence['errors'].append({'identity':identity, 'kind':kind,
                                                            'status':reply.status_code,
                                                            'detail':reply.text[:160]})
                        return None
                    hint = parse_retry_after(reply.headers.get('Retry-After')) if reply is not None else None
                    await asyncio.sleep((hint or 0) + random.uniform(retry/2, retry))
                    retry = min(2, retry*2)
                recovery_evidence['errors'].append({'identity':identity,'kind':kind,'reason':'deadline'})
                return None

            await asyncio.sleep(random.uniform(0, 1))
            while time.monotonic() < min(finish_at, started+seconds):
                body = {'generation':generation, 'sequence':sequence, 'identity':bound,
                        'state':'running', 'elapsed_seconds':max(0,time.monotonic()-started)}
                reply = await reliable('recovery_control', '/recovery/reconcile', body)
                if reply is None or reply.get('action') != 'track':
                    recovery_evidence['errors'].append({'identity':identity,
                                                        'kind':'recovery_control',
                                                        'reason':'track_not_confirmed'})
                    return
                sequence += 1
                await asyncio.sleep(2)
            if identity not in lifecycle_ids:
                return
            async with optional_report_locks[identity]:
                recovered.add(identity)
            exit_fact = {'generation':generation, 'sequence':sequence, 'identity':bound,
                         'state':'exited', 'exit_code':0,
                         'elapsed_seconds':max(0,time.monotonic()-started)}
            reply = await reliable('recovery_exit', '/recovery/reconcile', exit_fact)
            if reply is None or reply.get('action') != 'retain':
                recovery_evidence['errors'].append({'identity':identity,
                                                    'kind':'recovery_exit',
                                                    'reason':'exit_not_retained'})
                return
            proof = {'generation':generation, 'identity':bound}
            complete = await reliable('recovery_complete', '/complete',
                                      {'recovery':proof,'pending_results':True,
                                       'status':'SUCCEEDED','exit_code':0})
            if complete is None or complete.get('status') != 'SUCCEEDED':
                return
            recovery_evidence['completed'] += 1
            completed_at = time.monotonic()
            cleanup = await reliable('recovery_cleanup', '/cleanup', proof)
            if cleanup is None or not cleanup.get('cleanup_confirmed'):
                return
            recovery_evidence['cleanup_confirmed'] += 1
            release_latencies.append(time.monotonic()-completed_at)
            result = {'generation':generation,'kind':'task_result','sequence':0,'apply':True,
                      'body':{'success':True,'metrics':{},'summary':'simulated load result'}}
            accepted = await reliable('recovery_result', '/recovery/results', result)
            if accepted is None or accepted.get('application') != 'applied':
                return
            duplicate = await reliable('recovery_result_duplicate', '/recovery/results', result)
            if duplicate is None or not duplicate.get('duplicate'):
                return
            recovery_evidence['duplicate_results'] += 1
            sealed = await reliable('recovery_results_finished', '/recovery/results-finished', proof)
            if sealed is not None and sealed.get('result_sync') == 'complete':
                recovery_evidence['result_sync_complete'] += 1
        async def command_worker(identity, token):
            nonlocal worst_heartbeat_age
            current, result_sent = None, False
            claimed_at = None
            latest_heartbeat[identity] = latest_sample[identity] = started
            await asyncio.sleep(random.uniform(0, idle_claim_seconds))
            while time.monotonic()-started < seconds:
                heartbeat = {'slot_id':identity, 'telemetry':{'free_vram_mb':1024, 'gpu_utilization':0}}
                if current:
                    heartbeat['execution_id'] = current['execution_id']
                else:
                    heartbeat['storage'] = {'revision':0, 'applied_revision':0, 'status':'applied',
                                            'effective':{'workspace_root':'/tmp/load/work', 'assets_root':'/tmp/load/assets',
                                                         'cache_root':'/tmp/load/cache'}}
                    heartbeat['runtime'] = {'revision':0, 'applied_revision':0, 'effective':{'images':['load:command']},
                                            'images':{'load:command':{'ready':True}}}
                reply = await post('heartbeat', identity, token, 'worker/heartbeat', heartbeat)
                worst_heartbeat_age = max(worst_heartbeat_age, time.monotonic()-latest_heartbeat[identity])
                if reply is None or reply.status_code != 200:
                    await asyncio.sleep(.25)
                    continue
                latest_heartbeat[identity] = latest_sample[identity] = time.monotonic()
                if current is None:
                    reply = await post('claim', identity, token, f'worker/slots/{identity}/claim', {'known_execution_ids':[]})
                    if reply is not None and reply.status_code == 200:
                        current = reply.json().get('task')
                        result_sent = False
                        if current:
                            claimed_at = time.monotonic()
                else:
                    execution_id = current['execution_id']
                    if time.monotonic() - claimed_at < command_hold_seconds:
                        await asyncio.sleep(.5)
                        continue
                    success = (batch_cases > 1 or
                               any(e['job_id'] == current['job_id'] for e in ui_evidence['executions']))
                    path = f'worker/executions/{execution_id}'
                    if not result_sent:
                        body = {'success':success, 'metrics':{}, 'summary':'simulated API lifecycle'}
                        reply = await post('result', identity, token, path+'/task-result', body)
                        if reply is not None and reply.status_code == 200:
                            duplicate = await post('result_duplicate', identity, token, path+'/task-result', body)
                            if duplicate is not None and duplicate.status_code == 200 and duplicate.json().get('duplicate'):
                                ui_evidence['duplicate_results'] += 1
                                result_sent = True
                    if result_sent:
                        status = 'SUCCEEDED' if success else 'FAILED'
                        reply = await post('complete', identity, token, path+'/complete',
                                           {'status':status, 'exit_code':0 if success else 1})
                        if reply is not None and reply.status_code == 200:
                            ui_evidence['executions'].append({'execution_id':execution_id, 'job_id':current['job_id'], 'status':status})
                            current = None
                            claimed_at = None
                await asyncio.sleep(.5 if current else idle_claim_seconds)
        async def ui_traffic():
            if user_token is None:
                return
            identity = identities[0][0]
            headers = {'Authorization': 'Bearer '+user_token}
            async def request(kind, method, path, body=None):
                began = time.monotonic()
                try:
                    reply = await ui_client.request(method, '/api/v1/'+path, json=body, headers=headers)
                    code = str(reply.status_code)
                    codes[code] += 1
                    by_kind[kind][code] += 1
                    if reply.status_code == 503:
                        reason = reply.json().get('reason', 'unknown')
                        reasons[reason] += 1
                        busy_by_kind[kind][reason] += 1
                    if reply.status_code in (200, 201):
                        successful_latency[kind].append(time.monotonic()-began)
                    else:
                        ui_evidence['errors'].append({'kind':kind, 'status':reply.status_code,
                                                      'detail':reply.text[:300]})
                    return reply
                except httpx.TransportError as exc:
                    codes['transport_error'] += 1
                    by_kind[kind]['transport_error'] += 1
                    ui_evidence['errors'].append({'kind':kind, 'error':type(exc).__name__})
                    return None
                finally:
                    latency[kind].append(time.monotonic()-began)
            body = {'title':'mixed-load', 'pool_id':'p', 'max_concurrent':command_workers,
                    'client':{'template_id':'load-template', 'service_mode':'none', 'task_name':'load'},
                    'cases':[{'case_id':f'queued-{i+1}', 'task_name':'load', 'episodes':1}
                             for i in range(batch_cases)]}
            async def submit_batch():
                created = await request('ui_submit', 'POST', 'batches', body)
                if created is None or created.status_code != 201:
                    return None
                batch_id = created.json()['id']
                ui_evidence.update(batch_id=batch_id, retried=False, last_batch_status=None)
                ui_evidence['batch_count'] += 1
                for action, revision in (('pause', 0), ('resume', 1)):
                    reply = await request('ui_control', 'POST', f'batches/{batch_id}/control',
                                          {'action':action, 'expected_revision':revision})
                    if reply is None or reply.status_code != 200:
                        return None
                    ui_evidence['control_revisions'].append(reply.json()['control_revision'])
                return batch_id
            batch_id = await submit_batch()
            if batch_id is None:
                return
            while time.monotonic()-started < seconds:
                await request('ui_list', 'GET', 'batches?view=summary')
                if recovery_grants:
                    artifact_id = running_identities[-1][0]
                    artifacts = await request('ui_artifacts', 'GET',
                                              f'jobs/{artifact_id}/attempts/{artifact_id}/artifacts')
                    if artifacts is not None and artifacts.status_code == 200:
                        ui_evidence['artifact_seen'] |= any(item['name'] == 'load.json'
                                                            for item in artifacts.json())
                detail = await request('ui_detail', 'GET', f'batches/{batch_id}')
                monitor = await request('ui_monitor', 'GET', f'batches/{batch_id}/monitor')
                if monitor is not None and monitor.status_code == 200:
                    ui_evidence['last_monitor'] = [{'status':case['status'], 'reasons':case['reason_codes'],
                                                    'waiting':case['waiting']} for case in monitor.json()['cases']]
                if detail is not None and detail.status_code == 200:
                    batch = detail.json()
                    ui_evidence['case_count_seen'] = max(ui_evidence['case_count_seen'],
                                                         len(batch['cases']))
                    ui_evidence['max_pending_count'] = max(ui_evidence['max_pending_count'],
                                                           batch['pending_count'])
                    ui_evidence['last_batch_status'] = batch['status']
                    if batch_cases == 1 and batch['status'] == 'FAILED' and not ui_evidence['retried']:
                        failed = batch['cases'][0]
                        retry = await request('ui_retry', 'POST', f'batches/{batch_id}/retry-failed',
                                              {'items':[{'job_id':failed['job_id'], 'expected_run_id':failed['run_id']}]})
                        if retry is not None and retry.status_code == 200:
                            ui_evidence['retried'] = retry.json()['retried'] == 1
                            ui_evidence['retry_count'] += int(ui_evidence['retried'])
                    if batch['status'] == 'SUCCEEDED' and batch_id not in ui_evidence['completed_batches']:
                        ui_evidence['completed_batches'].append(batch_id)
                    if batch['status'] == 'SUCCEEDED' and continuous_batches and started+seconds-time.monotonic() > 15:
                        batch_id = await submit_batch()
                        if batch_id is None:
                            return
                await asyncio.sleep(2)
        log_tasks = ([asyncio.create_task(task) for item in running_identities
                      if item[0] not in lifecycle_ids for task in
                      (produce_logs(item[0]), send_logs(*item[:2]))] if independent_logs else [])
        agent_identities = ([item for item in identities
                             if not node_groups or item[0] == node_groups[item[0]]['slots'][0]]
                            if agent_telemetry else [])
        agent_tasks = [asyncio.create_task(agent(*item)) for item in agent_identities]
        loop_task = asyncio.create_task(sample_loop_lag())
        progress_task = asyncio.create_task(sample_progress()) if progress_path else None
        ui_task = asyncio.create_task(ui_traffic()) if user_token else None
        def record_web(code, elapsed, reason, path):
            kind = "team_web_" + ("monitor" if path.endswith("/monitor") else "detail" if path.startswith("batches/") else "list" if path.startswith("batches?") else "pools")
            by_kind[kind][code] += 1
            latency[kind].append(elapsed)
            if code == "200":successful_latency[kind].append(elapsed)
            codes[code] += 1
            by_kind['team_web'][code] += 1
            latency['team_web'].append(elapsed)
            if code == '200':
                successful_latency['team_web'].append(elapsed)
            if code == 'transport_error':
                web_transport_errors[reason or 'unknown'] += 1
                web_transport_error_windows[str(int((time.monotonic() - started)//30)*30)] += 1
            elif reason:
                reasons[reason] += 1
                busy_by_kind['team_web'][reason] += 1
        from backend.tests.team_web_load import browse
        team_task = asyncio.create_task(browse(base, web_tokens, seconds,
            lambda: ui_evidence['batch_id'], record_web)) if web_tokens else None
        worker_tasks = [worker(*item[:2]) for item in running_identities]
        control_tasks = [recovery_control(*item[:2]) for item in running_identities] if recovery_grants else []
        if user_token:
            worker_tasks.extend(command_worker(*item[:2]) for item in identities[-command_workers:])
        await asyncio.gather(*worker_tasks, *control_tasks)
        end = time.monotonic()
        await loop_task
        if progress_task:
            await progress_task
        if team_task:
            await team_task
        if ui_task:
            await ui_task
        if agent_tasks:
            await asyncio.gather(*agent_tasks)
        if log_tasks:
            await asyncio.gather(*log_tasks)
            max_log_age = max(max_log_age, max((time.monotonic() - created for queue in log_queues.values()
                                                for created, _ in queue), default=0))
    return {'requests': dict(codes), 'busy_reasons': dict(reasons),
            'web_transport_errors': dict(web_transport_errors),
            'web_transport_error_windows_seconds': dict(web_transport_error_windows),
            'requests_by_kind': {k:dict(v) for k,v in by_kind.items()},
            'busy_by_kind': {k:dict(v) for k,v in busy_by_kind.items()},
            'successful_latency_seconds': {k:{'p95':percentile(v,.95), 'p99':percentile(v,.99), 'max':max(v)} for k,v in successful_latency.items()},
            'latency_seconds': {k: {'p95':percentile(v,.95), 'p99':percentile(v,.99), 'max':max(v)} for k,v in latency.items()},
            'max_heartbeat_gap_seconds': max(worst_heartbeat_age, max(end-v for v in latest_heartbeat.values())),
            'max_final_sample_age_seconds': max(end-v for v in latest_sample.values()),
            'max_final_agent_age_seconds': max((max(0, end-v) for v in latest_agent.values()), default=None),
            'load_driver_loop_lag_seconds': {'p95': percentile(loop_lag, .95), 'p99': percentile(loop_lag, .99),
                                             'max': max(loop_lag, default=0)},
            'http_phases_seconds': {k: {'p95': percentile(v, .95), 'p99': percentile(v, .99),
                                         'max': max(v), 'samples': len(v)} for k, v in http_phases.items()},
            'progress': progress,
            'ui_evidence': ui_evidence if user_token else None,
            'recovery_evidence': ({**recovery_evidence,
                'release_latency_seconds':{'p95':percentile(release_latencies,.95),
                                           'p99':percentile(release_latencies,.99),
                                           'max':max(release_latencies,default=None)}}
                                  if recovery_grants else None),
            'independent_logs': independent_logs,
            'log_generation': {**dict(log_stats), 'max_pending_chunks_per_worker': max_log_pending,
                               'final_pending_chunks': sum(len(queue) for queue in log_queues.values()),
                               'max_pending_age_seconds': max_log_age} if independent_logs else None}


def minimum_client_nofile(workers, web_users, mixed_ui):
    """Reserve the HTTP clients' possible sockets plus a small driver margin."""
    return 4 * (workers + web_users) + (4 if mixed_ui else 0) + 16


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--grades', default='50,100,200')
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--idle-claim-seconds', type=float, default=.5, help='idle command-slot polling; use 2 for production Worker cadence')
    parser.add_argument('--command-hold-seconds', type=float, default=0,
                        help='keep claimed command tasks running before reporting results (0..60 seconds)')
    parser.add_argument('--web-users', type=int, default=0, help='distinct admin users browsing shared batch pages every five seconds')
    parser.add_argument('--capacity-config', help='JSON object of priority Settings capacity overrides')
    parser.add_argument('--log-bytes', type=int, default=1024)
    parser.add_argument('--varied-logs', action='store_true', help='distinct deterministic structured log lines, instead of highly compressible repeated characters')
    parser.add_argument('--log-interval', type=float, default=2)
    parser.add_argument('--output', required=True)
    parser.add_argument('--ordinary-pool', type=int, default=10)
    parser.add_argument('--control-pool', type=int, default=5)
    parser.add_argument('--observation-pool', type=int, default=2)
    parser.add_argument('--result-pool', type=int, default=None)
    parser.add_argument('--independent-logs', action='store_true')
    parser.add_argument('--drain-seconds', type=int, default=5)
    parser.add_argument('--trace-http-phases', action='store_true')
    parser.add_argument('--client-per-worker', action='store_true')
    parser.add_argument('--agent-telemetry', action='store_true')
    parser.add_argument('--mixed-ui', action='store_true',
                        help='enable real scheduler plus authenticated batch submit/control/list/detail')
    parser.add_argument('--recovery-load', action='store_true',
                        help='simulate negotiated execution reconciliation, completion, cleanup and late reliable results')
    parser.add_argument('--cards-per-node', type=int, default=1,
                        help='group simulated card slots under one node identity (recovery load only)')
    parser.add_argument('--command-workers', type=int, default=1,
                        help='free slots that claim and complete queued batch cases')
    parser.add_argument('--batch-cases', type=int, default=1,
                        help='number of tasks in one submitted batch, maximum 100')
    parser.add_argument('--continuous-batches', action='store_true', help='repeat submit/fail/retry/succeed during mixed UI load')
    parser.add_argument('--bulk-outage-seconds', type=int, default=0, help='stop bulk role 30 seconds into load, then restart')
    parser.add_argument('--control-outage-seconds', type=int, default=0,
                        help='stop execution-control role 30 seconds into recovery load, then restart')
    parser.add_argument('--postgres-container', help='optional disposable PostgreSQL container for read-only docker stats')
    parser.add_argument('--progress-every-30s', action='store_true')
    parser.add_argument('--split-api', action='store_true',
                        help='run two isolated API processes behind the production Nginx routing config')
    parser.add_argument('--priority-api', action='store_true',
                        help='run four role-specific API processes and the generated Nginx gateway')
    parser.add_argument('--bulk-extra', action='store_true',
                        help='load-test only: add a second bulk-transfer API process to compare throughput')
    parser.add_argument('--role-aware-pools', action='store_true',
                        help='use the split Compose role-specific pool profile')
    parser.add_argument('--no-backpressure', action='store_true',
                        help='use legacy DB sessions without category admission for comparison')
    parser.add_argument('--no-admission', action='store_true',
                        help='retain bounded DB pools and deadlines but do not reject at category entrance')
    args = parser.parse_args()
    grades = [int(x) for x in args.grades.split(',')]
    if args.bulk_outage_seconds < 0 or (args.bulk_outage_seconds and (not args.priority_api or not args.independent_logs or args.seconds < 60+args.bulk_outage_seconds)):
        parser.error('--bulk-outage-seconds requires four roles, independent logs and at least 60 + outage seconds')
    if (args.control_outage_seconds < 0 or args.control_outage_seconds and
            (not args.recovery_load or args.bulk_outage_seconds or args.seconds < 60+args.control_outage_seconds)):
        parser.error('--control-outage-seconds requires recovery load, no bulk outage and at least 60 + outage seconds')
    if args.continuous_batches and (not args.mixed_ui or args.seconds < 30):
        parser.error('--continuous-batches requires --mixed-ui and at least 30 seconds')
    if args.recovery_load and (not args.priority_api or not args.mixed_ui or
                               not args.agent_telemetry or args.seconds < 30):
        parser.error('--recovery-load requires four roles, mixed UI, Agent telemetry and at least 30 seconds')
    if args.cards_per_node < 1 or (args.cards_per_node != 1 and not args.recovery_load):
        parser.error('--cards-per-node must be positive and currently requires --recovery-load')
    if (args.command_workers < 1 or (args.mixed_ui and args.command_workers >= min(grades)) or
            args.batch_cases < 1 or args.batch_cases > 100 or
            (args.command_workers != 1 or args.batch_cases != 1) and not args.mixed_ui or
            args.batch_cases > 1 and args.continuous_batches):
        parser.error('large batches require --mixed-ui, 1..100 cases, free command slots and no --continuous-batches')
    if args.priority_api and (args.split_api or args.role_aware_pools or args.no_admission or args.no_backpressure):
        parser.error('--priority-api requires its own pools and admission, without --split-api')
    if args.bulk_extra and (not args.priority_api or args.bulk_outage_seconds):
        parser.error('--bulk-extra requires --priority-api and no bulk outage')
    if args.role_aware_pools and not args.split_api:
        parser.error('--role-aware-pools requires --split-api')
    if args.role_aware_pools and args.no_backpressure:
        parser.error('--role-aware-pools requires database backpressure')
    if args.no_backpressure and args.no_admission:
        parser.error('--no-admission requires database backpressure')
    if not .1 <= args.idle_claim_seconds <= 10:
        parser.error('idle claim seconds must be between .1 and 10')
    if not 0 <= args.command_hold_seconds <= 60 or not math.isfinite(args.command_hold_seconds):
        parser.error('command hold seconds must be finite and between 0 and 60')
    if not 0 <= args.web_users <= 1000 or args.web_users and not args.mixed_ui:
        parser.error('--web-users requires mixed UI and a count between 0 and 1000')
    capacity = json.loads(Path(args.capacity_config).read_text()) if args.capacity_config else {}
    allowed_capacity = {'admission_limits','queue_limits','queue_waits','pool_waits','request_budgets','pool_sizes','connection_budget','interaction_replicas'}
    if not isinstance(capacity, dict) or set(capacity) - allowed_capacity or capacity and not args.priority_api:
        parser.error('capacity-config supports priority capacity settings only')
    priority_options = dict(pool_sizes=priority_runtime.SCALED_POOLS, processes=5, control_replicas=2)
    priority_options.update(capacity)
    priority_options['processes'] = 4 + priority_options.get('interaction_replicas', 1)
    priority_settings = (priority_runtime.Settings('interaction', False, **priority_options)
                         if args.priority_api else None)
    profiles = ({role: priority_runtime.Settings(role, role == 'worker-data',
                                                 **priority_options).active_pool_sizes
                 for role in priority_runtime.ROLE_POOLS} if args.priority_api else
                pool_profiles(args.ordinary_pool, args.control_pool, args.observation_pool,
                             split_api=args.split_api, role_aware=args.role_aware_pools,
                             scheduler_enabled=args.mixed_ui, result=args.result_pool))
    process_count = (priority_settings.processes + int(args.bulk_extra) if args.priority_api else len(profiles))
    configured_connections = ((priority_settings.configured_connections +
                               (priority_settings.pool_sizes['bulk_transfer'] if args.bulk_extra else 0)) if priority_settings else
                              sum(sum(p.values()) for p in profiles.values()))
    if min(grades) < 1 or args.seconds < 5 or not 1 <= args.log_bytes <= 16384 or not .1 <= args.log_interval <= 60 or args.drain_seconds < 0 or any(value < 1 for profile in profiles.values() for value in profile.values()) or configured_connections > (priority_settings.connection_budget if priority_settings else 80) or (not args.priority_api and any(sum(p.values()) * process_count > 80 for p in profiles.values())):
        parser.error('positive grades, seconds >= 5 and log bytes 1..16384 required')
    soft_nofile, _ = resource.getrlimit(resource.RLIMIT_NOFILE)
    minimum_nofile = minimum_client_nofile(max(grades), args.web_users, args.mixed_ui)
    if soft_nofile < minimum_nofile:
        parser.error(f'open-files soft limit {soft_nofile} is below HTTP load budget {minimum_nofile}; raise ulimit -n for this disposable run')
    url = make_url(os.environ['LOG_TEST_DATABASE_URL'])
    if url.get_backend_name() != 'postgresql':
        parser.error('requires disposable PostgreSQL admin URL')
    output = Path(args.output);output.mkdir(parents=True, exist_ok=True)
    admin = create_engine(url, isolation_level='AUTOCOMMIT')
    summary = {'hardware': {'machine':platform.machine(), 'cpu_count':os.cpu_count()},
               'api_processes':process_count,
               'gateway':'nginx host-network test container' if args.split_api or args.priority_api else None,
               'pool_ordinary':args.ordinary_pool, 'pool_control':args.control_pool,
               'pool_observation':args.observation_pool,
               'pool_result':args.result_pool if args.result_pool is not None else (3 if args.role_aware_pools else 2),
               'pool_scheduler':1,
               'pool_by_process':({**profiles, 'execution-control-extra':profiles['execution-control'],
                                    **({'bulk-transfer-extra':profiles['bulk-transfer']} if args.bulk_extra else {}),
                                    **{f'interaction-extra-{i}':profiles['interaction'] for i in range(priority_settings.interaction_replicas-1)}}
                                  if args.priority_api else profiles) if not args.no_backpressure else None,
               'configured_total_connections':configured_connections,
               'db_backpressure_enabled':not args.no_backpressure,
               'db_admission_enabled':not (args.no_admission or args.no_backpressure),
               'cards_per_node':args.cards_per_node,
               'command_workers':args.command_workers, 'batch_cases':args.batch_cases,
               'command_hold_seconds':args.command_hold_seconds,
               'seconds_per_grade':args.seconds, 'log_bytes_per_worker_per_cycle':args.log_bytes,
               'log_payload_style':'varied-structured' if args.varied_logs else 'repeated-character',
               'target_cycle_seconds':2, 'scheduler_enabled':args.mixed_ui or args.priority_api,
               'priority_api':args.priority_api, 'bulk_extra':args.bulk_extra,
               'log_interval_seconds':args.log_interval,
               'independent_logs':args.independent_logs, 'log_drain_seconds':args.drain_seconds if args.independent_logs else 0,
               'trace_http_phases':args.trace_http_phases,
               'client_per_worker':args.client_per_worker,
               'agent_telemetry':args.agent_telemetry,
               'idle_claim_seconds':args.idle_claim_seconds, 'mixed_ui':args.mixed_ui, 'web_users':args.web_users, 'capacity_overrides':capacity,
               'recovery_load':args.recovery_load,
               'continuous_batches':args.continuous_batches, 'bulk_outage_seconds':args.bulk_outage_seconds,
               'control_outage_seconds':args.control_outage_seconds,
               'scope':('real HTTP/PostgreSQL, simulated negotiated recovery control/finish/cleanup/late result; '
                        'about 10% of device slots complete during independent log traffic, others remain active; '
                        'real scheduler plus authenticated Web/Agent; excludes real business processes'
                        if args.recovery_load else
                        'real HTTP/PostgreSQL, simulated running services, authenticated batch submit/control/read/retry, '
                        'real scheduler and one simulated command Worker completing two attempts; excludes real business processes'
                        if args.mixed_ui else
                        'real HTTP/PostgreSQL, simulated running services and Agent telemetry; '
                        'excludes scheduler allocation and real business processes' if args.agent_telemetry else
                        'real HTTP/PostgreSQL, simulated running services; excludes scheduler allocation, Agent and real business processes'),
               'grades':[]}
    if args.priority_api:
        for key in ('pool_ordinary', 'pool_control', 'pool_observation', 'pool_result', 'pool_scheduler'):
            summary.pop(key)
    try:
        for count in grades:
            database = 'control_load_'+uuid.uuid4().hex
            with admin.connect() as c:c.execute(text('CREATE DATABASE '+database))
            target = url.set(database=database)
            engine = create_engine(target)
            processes = []
            role_processes = {}
            gateway = None
            try:
                seeded = seed(engine, count, agent_telemetry=args.agent_telemetry,
                              mixed_ui=args.mixed_ui, recovery_load=args.recovery_load,
                              cards_per_node=args.cards_per_node,
                              command_workers=args.command_workers)
                identities, recovery_grants, node_groups = seeded if args.recovery_load else (seeded, None, None)
                if args.web_users:
                    with sessionmaker(engine).begin() as s:
                        s.add_all(User(id='web-'+str(i),username='web-'+str(i),role='admin',password_hash='unused')
                                  for i in range(args.web_users))
                with engine.begin() as c:
                    c.execute(text("UPDATE evaluation_runs SET lease_expires_at=now()+interval '30 seconds'"))
                def free_port():
                    with socket.socket() as sock:
                        sock.bind(('127.0.0.1', 0))
                        return sock.getsockname()[1]
                ordinary_port = free_port()
                control_port = free_port() if args.split_api else None
                role_ports = {role: free_port() for role in profiles} if args.priority_api else {}
                control_extra_port = free_port() if args.priority_api else None
                bulk_extra_port = free_port() if args.bulk_extra else None
                interaction_extra_ports = [free_port() for _ in range(priority_settings.interaction_replicas-1)] if args.priority_api else []
                port = free_port() if args.split_api or args.priority_api else ordinary_port
                with ExitStack() as stack:
                    jwt_secret = uuid.uuid4().hex
                    env = {**os.environ, 'DATABASE_URL':target.render_as_string(hide_password=False), 'JWT_SECRET':jwt_secret,
                           'SCHEDULER_ENABLED':'1' if args.mixed_ui else '0',
                           'DB_BACKPRESSURE_ENABLED':'0' if args.no_backpressure else '1',
                           'DB_ADMISSION_ENABLED':'0' if args.no_admission else '1',
                           # Settings counts the four supported production roles. The
                           # second bulk process is isolated load-tool-only topology.
                           'API_DB_PROCESS_COUNT':str(priority_settings.processes if args.priority_api else process_count),
                           'DB_CONNECTION_BUDGET':str(priority_settings.connection_budget if priority_settings else 80), 'DB_BUDGET_DIAGNOSTICS':'1'}
                    if args.mixed_ui:
                        env.update(MAX_ACTIVE_GLOBAL=str(count+10), MAX_ACTIVE_PER_USER=str(count+10))
                    if args.priority_api:
                        for key,value in capacity.items():
                            env[{'connection_budget':'DB_CONNECTION_BUDGET','interaction_replicas':'API_INTERACTION_REPLICAS'}.get(key,'PRIORITY_API_'+key.upper())] = json.dumps(value)
                        env['PRIORITY_API_PEERS'] = json.dumps({role:f'http://127.0.0.1:{p}' for role,p in role_ports.items()})
                        env['PRIORITY_API_POOL_SIZES'] = json.dumps(priority_settings.pool_sizes)
                        env['API_CONTROL_REPLICAS'] = '2'
                    def launch(role, api_port):
                        api_env = {**env, 'DB_POOL_SIZE': str(args.ordinary_pool),
                                   'DB_CONTROL_POOL_SIZE': str(args.control_pool),
                                   'DB_OBSERVATION_POOL_SIZE': str(args.observation_pool),
                                   'DB_RESULT_POOL_SIZE': str(args.result_pool if args.result_pool is not None else
                                                              (3 if args.role_aware_pools else 2)),
                                   'DB_SCHEDULER_POOL_SIZE': '1'}
                        if args.split_api and args.role_aware_pools:
                            api_env['API_ROUTE_GROUP'] = role
                        if args.split_api and role == 'control':
                            api_env['SCHEDULER_ENABLED'] = '0'
                        if args.priority_api:
                            api_env['API_ROUTE_GROUP'] = 'execution-control' if role == 'execution-control-extra' else 'interaction' if role.startswith('interaction-extra-') else 'bulk-transfer' if role == 'bulk-transfer-extra' else role
                            api_env['SCHEDULER_ENABLED'] = '1' if role == 'worker-data' else '0'
                        log_name = (f'api-{role}-{count}.log' if args.split_api or args.priority_api else f'api-{count}.log')
                        log = stack.enter_context((output/log_name).open('a' if role in role_processes else 'w'))
                        process = subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.main:app',
                                                    '--host','127.0.0.1','--port',str(api_port),'--no-access-log'],
                                                   env=api_env, stdout=log, stderr=log)
                        processes.append(process)
                        role_processes[role] = process
                    if args.priority_api:
                        for role, api_port in role_ports.items():
                            launch(role, api_port)
                        launch('execution-control-extra', control_extra_port)
                        if args.bulk_extra:
                            launch('bulk-transfer-extra', bulk_extra_port)
                        for i, extra_port in enumerate(interaction_extra_ports):
                            launch(f'interaction-extra-{i}', extra_port)
                    else:
                        launch('ordinary', ordinary_port)
                    if args.split_api:
                        launch('control', control_port)
                    if args.split_api or args.priority_api:
                        deploy = Path(__file__).resolve().parents[2] / 'deploy'
                        config = (render_priority_gateway_config(role_ports, port, control_extra_port,
                                                                 interaction_extra_ports, bulk_extra_port) if args.priority_api else
                                  render_gateway_config((deploy/'split-api-nginx.conf').read_text(),
                                                       ordinary_port, control_port, port))
                        gateway_config = output/f'gateway-{count}.conf'
                        gateway_config.write_text(config)
                        gateway = 'mini-eval-load-gateway-'+uuid.uuid4().hex[:12]
                        subprocess.run(['docker','run','-d','--rm','--network','host','--name',gateway,
                                        '-v',f'{gateway_config.resolve()}:/etc/nginx/conf.d/default.conf:ro',
                                        '-v',f'{(deploy/"proxy-common.inc").resolve()}:/etc/nginx/conf.d/proxy-common.inc:ro',
                                        'm.daocloud.io/docker.io/library/nginx:1.27-alpine'],
                                       check=True, stdout=subprocess.DEVNULL)
                    base = f'http://127.0.0.1:{port}'
                    if args.priority_api:
                        # The role-level health only probes one peer per role.
                        # Wait for every extra replica before sending user traffic.
                        for extra_port in [control_extra_port, *interaction_extra_ports,
                                           *([bulk_extra_port] if args.bulk_extra else [])]:
                            for _ in range(100):
                                try:
                                    r = httpx.get(f'http://127.0.0.1:{extra_port}/healthz',trust_env=False,timeout=1)
                                    if r.status_code == 200: break
                                except httpx.TransportError: pass
                                time.sleep(.1)
                            else: raise RuntimeError('extra replica did not become ready')
                    for _ in range(100):
                        if any(process.poll() is not None for process in processes):
                            raise RuntimeError('isolated API exited; inspect API log')
                        try:
                            if (httpx.get(base+'/openapi.json',timeout=1,trust_env=False).status_code == 200 and
                                    httpx.get(base+'/healthz',timeout=1,trust_env=False).status_code == 200):
                                break
                        except httpx.TransportError:pass
                        time.sleep(.1)
                    else:raise RuntimeError('isolated API did not become ready')
                    work = exercise(base, identities, args.seconds, args.log_bytes,
                                                  independent_logs=args.independent_logs,
                                                  drain_seconds=args.drain_seconds if args.independent_logs else 0,
                                                  trace_http_phases=args.trace_http_phases,
                                                  client_per_worker=args.client_per_worker,
                                                  agent_telemetry=args.agent_telemetry,
                                                  progress_path=output/f'progress-{count}.jsonl' if args.progress_every_30s else None,
                                                  log_interval=args.log_interval,
                                                  continuous_batches=args.continuous_batches, varied_logs=args.varied_logs,
                                                  recovery_grants=recovery_grants, node_groups=node_groups,
                                                  command_workers=args.command_workers, batch_cases=args.batch_cases,
                                                  command_hold_seconds=args.command_hold_seconds,
                                                  idle_claim_seconds=args.idle_claim_seconds,
                                                  web_tokens=[jwt.encode({'sub':'web-'+str(i),'exp':int(time.time())+3600},jwt_secret,algorithm='HS256')
                                                              for i in range(args.web_users)],
                                                  user_token=jwt.encode({'sub':'u', 'exp':int(time.time())+3600}, jwt_secret,
                                                                        algorithm='HS256') if args.mixed_ui else None)
                    if args.bulk_outage_seconds:
                        def stop_bulk():
                            victim = role_processes['bulk-transfer']
                            victim.terminate()
                            victim.wait(timeout=10)
                        work = with_outage(work, stop_bulk, lambda: launch('bulk-transfer', role_ports['bulk-transfer']),
                                           after=30, duration=args.bulk_outage_seconds)
                    if args.control_outage_seconds:
                        def stop_control():
                            for role in ('execution-control', 'execution-control-extra'):
                                victim = role_processes[role]
                                victim.terminate()
                                victim.wait(timeout=10)
                        def restart_control():
                            launch('execution-control', role_ports['execution-control'])
                            launch('execution-control-extra', control_extra_port)
                        work = with_outage(work, stop_control,
                                           restart_control,
                                           after=30, duration=args.control_outage_seconds)
                    result = asyncio.run(observe(work, engine, container=args.postgres_container,
                                                 load_seconds=args.seconds))
                    if args.priority_api:
                        topology = {}
                        for role, api_port in role_ports.items():
                            direct = f'http://127.0.0.1:{api_port}'
                            wrong = '/api/v1/batches' if role != 'interaction' else '/api/v1/worker/heartbeat'
                            topology[role] = {'health':httpx.get(direct+'/healthz', trust_env=False).json(),
                                              'ready_status':httpx.get(direct+'/readyz', trust_env=False).status_code,
                                              'wrong_route_status':httpx.post(direct+wrong, trust_env=False).status_code}
                        result['role_topology'] = topology
                        if args.bulk_extra:
                            result['role_failure'] = {'skipped': 'bulk replica capacity comparison'}
                        else:
                            victim = role_processes['bulk-transfer']
                            victim.terminate()
                            victim.wait(timeout=10)
                            result['role_failure'] = {
                                'gateway_health_when_bulk_stopped':httpx.get(base+'/healthz', trust_env=False).status_code,
                                'bulk_request_when_stopped':httpx.post(base+'/api/v1/worker/executions/test/logs-v2',
                                                                     trust_env=False).status_code,
                                'heartbeat_when_bulk_stopped':httpx.post(base+'/api/v1/worker/heartbeat',
                                    headers={'Authorization':'Bearer '+identities[0][1]},
                                    json={'slot_id':identities[0][0]}, trust_env=False).status_code}
                            launch('bulk-transfer', role_ports['bulk-transfer'])
                            for _ in range(50):
                                healthy = httpx.get(base+'/healthz', trust_env=False, timeout=2).status_code
                                if healthy == 200:
                                    break
                                time.sleep(.1)
                            result['role_failure']['gateway_health_after_restart'] = healthy
                api_logs = [(output/name).read_text() for name in
                            (api_log_names(count, args.split_api, args.priority_api) +
                             ([f'api-bulk-transfer-extra-{count}.log'] if args.bulk_extra else []) +
                             [f'api-interaction-extra-{i}-{count}.log' for i in range(len(interaction_extra_ports))])]
                checkouts = [float(v) for log in api_logs for v in
                             re.findall(r'database checkout category=\w+ wait_ms=([0-9.]+)', log)]
                result['initial_checkout_wait_ms'] = {'p95':percentile(checkouts,.95), 'p99':percentile(checkouts,.99)}
                result['pool_checkout_wait_ms'], result['admission_wait_ms'] = capacity_waits(api_logs)
                with engine.connect() as c:
                    result['expired_leases_after_drain'] = c.scalar(text("SELECT count(*) FROM evaluation_runs WHERE lease_expires_at <= now() AND status IN ('STARTING','RUNNING','CANCEL_REQUESTED')"))
                    result['expired_leases'] = expired_leases_during_load(result['database_samples'])
                    result['telemetry_stale'] = c.scalar(text("SELECT count(*) FROM gpu_slots WHERE telemetry_at IS NULL OR telemetry_at < now()-interval '45 seconds'"))
                    result['database_max_connections'] = int(c.scalar(text('SHOW max_connections')))
                    result['database_connections_at_end'] = c.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()"))
                    result['executions'] = c.scalar(text('SELECT count(*) FROM evaluation_runs'))
                    if args.mixed_ui and result['ui_evidence']['batch_id']:
                        spans = c.execute(select(Run.started_at, Run.finished_at).join(Job, Run.job_id == Job.id)
                                          .where(Job.batch_id == result['ui_evidence']['batch_id'])).all()
                        result['batch_execution_peak'] = peak_execution_overlap(spans)
                        result['batch_full_overlap_seconds'] = full_execution_overlap_seconds(spans)
                    if args.recovery_load:
                        result['recovery_database'] = recovery_database_counts(c)
                result['workers'] = count
                result['device_workers'] = count
                result['node_count'] = (len({group['worker_id'] for group in node_groups.values()})
                                        if node_groups else count)
                result['passed'] = (result['expired_leases'] == 0 and result['telemetry_stale'] == 0 and
                    result['max_heartbeat_gap_seconds'] < 10 and all(v['p99'] < 5 for v in result['latency_seconds'].values()) and
                    acceptable_responses(result['requests_by_kind'], mixed_ui=args.mixed_ui,
                                         bulk_outage=args.bulk_outage_seconds,
                                         control_outage=bool(args.control_outage_seconds)))
                if args.web_users:
                    team = result['requests_by_kind'].get('team_web', {})
                    result['passed'] = result['passed'] and set(team) == {'200'} and team.get('200',0) >= args.web_users
                if args.independent_logs:
                    logs = result['log_generation']
                    result['passed'] = (result['passed'] and logs.get('generated', 0) == logs.get('accepted', 0)
                                        and logs['final_pending_chunks'] == 0 and not logs.get('dropped', 0)
                                        and not logs.get('permanent_error', 0) and not logs.get('unexpected_ack', 0))
                if args.agent_telemetry:
                    result['passed'] = (result['passed'] and result['max_final_agent_age_seconds'] < 10
                                        and result['requests_by_kind'].get('agent', {}).get('200', 0) >=
                                        (count + args.cards_per_node - 1)//args.cards_per_node)
                if args.mixed_ui:
                    ui = result['ui_evidence']
                    if args.batch_cases > 1:
                        ui_ok = (ui['control_revisions'] == [1, 2] and not ui['errors']
                                 and ui['last_batch_status'] == 'SUCCEEDED'
                                 and len(ui['completed_batches']) == 1
                                 and ui['max_pending_count'] >= args.batch_cases-args.command_workers
                                 and len(ui['executions']) == args.batch_cases
                                 and all(e['status'] == 'SUCCEEDED' for e in ui['executions'])
                                 and ui['duplicate_results'] == args.batch_cases)
                    else:
                        ui_ok = (ui['control_revisions'] == [1, 2]*ui['batch_count'] and not ui['errors']
                                 and ui['retried'] and ui['last_batch_status'] == 'SUCCEEDED'
                                 and len(ui['completed_batches']) == ui['retry_count'] == ui['batch_count']
                                 and [e['status'] for e in ui['executions']] == ['FAILED', 'SUCCEEDED']*ui['batch_count']
                                 and ui['duplicate_results'] == 2*ui['batch_count'])
                    result['passed'] = (result['passed'] and ui['batch_id'] is not None
                                        and ui['case_count_seen'] == args.batch_cases and ui_ok
                                        and result['requests_by_kind'].get('ui_detail', {}).get('200', 0) > 0)
                if args.recovery_load:
                    recovery = result['recovery_evidence']
                    target = recovery['target_lifecycle']
                    result['passed'] = (result['passed'] and target > 0 and not recovery['errors']
                                        and recovery['completed'] == recovery['cleanup_confirmed'] ==
                                        recovery['result_sync_complete'] == recovery['duplicate_results'] == target
                                        and result['recovery_database'] == {
                                            'completed':target,'cleanup_confirmed':target,
                                            'result_sync_complete':target}
                                        and result['requests_by_kind'].get('recovery_control', {}).get('200', 0) >=
                                        count - (args.command_workers if args.mixed_ui else 0)
                                        and result['requests_by_kind'].get('artifact', {}).get('200', 0) > 0
                                        and ui['artifact_seen']
                                        and result['successful_latency_seconds'].get('recovery_control', {}).get('p99', 999) < 5
                                        and recovery['release_latency_seconds']['max'] is not None
                                        and recovery['release_latency_seconds']['max'] < 9)
                if args.priority_api:
                    result['passed'] = (result['passed'] and all(row['ready_status'] == 200 and row['wrong_route_status'] == 404
                                         for row in result['role_topology'].values()) and (result['role_failure'] == {
                                             'gateway_health_when_bulk_stopped':503, 'bulk_request_when_stopped':502,
                                             'heartbeat_when_bulk_stopped':200, 'gateway_health_after_restart':200}
                                             if not args.bulk_extra else
                                             result['role_failure'] == {'skipped': 'bulk replica capacity comparison'}))
                summary['grades'].append(result)
                (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
                print(json.dumps({'workers':count,'passed':result['passed'],'requests':result['requests']}),flush=True)
                if not result['passed']:break
            finally:
                if gateway:
                    subprocess.run(['docker','stop',gateway], check=False, stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
                for process in processes:
                    process.terminate()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                engine.dispose()
                with admin.connect() as c:c.execute(text('DROP DATABASE '+database+' WITH (FORCE)'))
    finally:admin.dispose()


if __name__ == '__main__':main()
