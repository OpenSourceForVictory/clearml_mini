"""Disposable real HTTP/PostgreSQL control-plane load. Never targets an existing database."""
import argparse
import asyncio
from collections import Counter, defaultdict, deque
from contextlib import AsyncExitStack, ExitStack
from datetime import timedelta
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import re
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
from backend.tests.control_plane_metrics import observe, with_outage


def seed(engine, count, *, agent_telemetry=False, mixed_ui=False,
         recovery_load=False, cards_per_node=1):
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
                       protocol_version=18 if mixed_ui and i == count-1 else 1,
                       client_images=['load:command']))
            s.flush()
            if mixed_ui and i == count-1:
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


def render_priority_gateway_config(ports, gateway_port):
    if set(ports) != set(priority_runtime.ROLE_POOLS):
        raise ValueError('priority gateway requires every role port')
    config = priority_gateway.render().replace('listen 8000;', f'listen 127.0.0.1:{gateway_port};')
    for role, port in ports.items():
        config = config.replace(priority_gateway.BACKENDS[role], f'127.0.0.1:{port}')
    return config


def api_log_names(count, split_api, priority_api=False):
    if priority_api:
        return [f'api-{role}-{count}.log' for role in priority_runtime.ROLE_POOLS]
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


def acceptable_responses(by_kind, *, mixed_ui, bulk_outage):
    for kind, counts in by_kind.items():
        allowed = {'200', '503'} | ({'201'} if mixed_ui else set())
        if bulk_outage and kind in ('logs', 'artifact'):
            allowed |= {'502', '504'}
        if set(counts) - allowed:
            return False
    return True


async def exercise(base, identities, seconds, log_bytes, *, independent_logs=False, drain_seconds=0,
                   trace_http_phases=False, client_per_worker=False, agent_telemetry=False,
                   progress_path=None, log_interval=2, user_token=None, continuous_batches=False,
                   varied_logs=False, recovery_grants=None, node_groups=None):
    latency, codes, reasons = defaultdict(list), Counter(), Counter()
    successful_latency = defaultdict(list)
    by_kind, busy_by_kind = defaultdict(Counter), defaultdict(Counter)
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
                   'executions': [], 'duplicate_results': 0, 'errors': []}
    running_identities = identities[:-1] if user_token else identities
    recovery_grants = recovery_grants or {}
    node_groups = node_groups or {}
    lifecycle_ids = set(item[0] for item in running_identities[:max(1, len(running_identities)//10)]) if recovery_grants else set()
    recovered = set()
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
                    reason = reply.json().get('reason', 'scheduler_lock')
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
                if identity in recovered:
                    await asyncio.sleep(max(.05, 2-(time.monotonic()-cycle)))
                    continue
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
            exit_fact = {'generation':generation, 'sequence':sequence, 'identity':bound,
                         'state':'exited', 'exit_code':0,
                         'elapsed_seconds':max(0,time.monotonic()-started)}
            reply = await reliable('recovery_exit', '/recovery/reconcile', exit_fact)
            if reply is None or reply.get('action') != 'retain':
                recovery_evidence['errors'].append({'identity':identity,
                                                    'kind':'recovery_exit',
                                                    'reason':'exit_not_retained'})
                return
            recovered.add(identity)
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
            latest_heartbeat[identity] = latest_sample[identity] = started
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
                else:
                    execution_id = current['execution_id']
                    success = any(e['job_id'] == current['job_id'] for e in ui_evidence['executions'])
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
                await asyncio.sleep(.5)
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
            body = {'title':'mixed-load', 'pool_id':'p',
                    'client':{'template_id':'load-template', 'service_mode':'none', 'task_name':'load'},
                    'cases':[{'case_id':'queued-1', 'task_name':'load', 'episodes':1}]}
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
                detail = await request('ui_detail', 'GET', f'batches/{batch_id}')
                monitor = await request('ui_monitor', 'GET', f'batches/{batch_id}/monitor')
                if monitor is not None and monitor.status_code == 200:
                    ui_evidence['last_monitor'] = [{'status':case['status'], 'reasons':case['reason_codes'],
                                                    'waiting':case['waiting']} for case in monitor.json()['cases']]
                if detail is not None and detail.status_code == 200:
                    batch = detail.json()
                    ui_evidence['last_batch_status'] = batch['status']
                    if batch['status'] == 'FAILED' and not ui_evidence['retried']:
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
        worker_tasks = [worker(*item[:2]) for item in running_identities]
        control_tasks = [recovery_control(*item[:2]) for item in running_identities] if recovery_grants else []
        if user_token:
            worker_tasks.append(command_worker(*identities[-1][:2]))
        await asyncio.gather(*worker_tasks, *control_tasks)
        end = time.monotonic()
        await loop_task
        if progress_task:
            await progress_task
        if ui_task:
            await ui_task
        if agent_tasks:
            await asyncio.gather(*agent_tasks)
        if log_tasks:
            await asyncio.gather(*log_tasks)
            max_log_age = max(max_log_age, max((time.monotonic() - created for queue in log_queues.values()
                                                for created, _ in queue), default=0))
    return {'requests': dict(codes), 'busy_reasons': dict(reasons),
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


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--grades', default='50,100,200')
    parser.add_argument('--seconds', type=int, default=30)
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
    parser.add_argument('--continuous-batches', action='store_true', help='repeat submit/fail/retry/succeed during mixed UI load')
    parser.add_argument('--bulk-outage-seconds', type=int, default=0, help='stop bulk role 30 seconds into load, then restart')
    parser.add_argument('--postgres-container', help='optional disposable PostgreSQL container for read-only docker stats')
    parser.add_argument('--progress-every-30s', action='store_true')
    parser.add_argument('--split-api', action='store_true',
                        help='run two isolated API processes behind the production Nginx routing config')
    parser.add_argument('--priority-api', action='store_true',
                        help='run four role-specific API processes and the generated Nginx gateway')
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
    if args.continuous_batches and (not args.mixed_ui or args.seconds < 30):
        parser.error('--continuous-batches requires --mixed-ui and at least 30 seconds')
    if args.recovery_load and (not args.priority_api or not args.mixed_ui or
                               not args.agent_telemetry or args.seconds < 30):
        parser.error('--recovery-load requires four roles, mixed UI, Agent telemetry and at least 30 seconds')
    if args.cards_per_node < 1 or (args.cards_per_node != 1 and not args.recovery_load):
        parser.error('--cards-per-node must be positive and currently requires --recovery-load')
    if args.priority_api and (args.split_api or args.role_aware_pools or args.no_admission or args.no_backpressure):
        parser.error('--priority-api requires its own pools and admission, without --split-api')
    if args.role_aware_pools and not args.split_api:
        parser.error('--role-aware-pools requires --split-api')
    if args.role_aware_pools and args.no_backpressure:
        parser.error('--role-aware-pools requires database backpressure')
    if args.no_backpressure and args.no_admission:
        parser.error('--no-admission requires database backpressure')
    profiles = ({role: priority_runtime.Settings(role, role == 'worker-data').active_pool_sizes
                 for role in priority_runtime.ROLE_POOLS} if args.priority_api else
                pool_profiles(args.ordinary_pool, args.control_pool, args.observation_pool,
                             split_api=args.split_api, role_aware=args.role_aware_pools,
                             scheduler_enabled=args.mixed_ui, result=args.result_pool))
    process_count = len(profiles)
    if min(grades) < 1 or args.seconds < 5 or not 1 <= args.log_bytes <= 16384 or not .1 <= args.log_interval <= 60 or args.drain_seconds < 0 or any(value < 1 for profile in profiles.values() for value in profile.values()) or sum(sum(p.values()) for p in profiles.values()) > 80 or (not args.priority_api and any(sum(p.values()) * process_count > 80 for p in profiles.values())):
        parser.error('positive grades, seconds >= 5 and log bytes 1..16384 required')
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
               'pool_by_process':profiles if not args.no_backpressure else None,
               'configured_total_connections':sum(sum(p.values()) for p in profiles.values()),
               'db_backpressure_enabled':not args.no_backpressure,
               'db_admission_enabled':not (args.no_admission or args.no_backpressure),
               'cards_per_node':args.cards_per_node,
               'seconds_per_grade':args.seconds, 'log_bytes_per_worker_per_cycle':args.log_bytes,
               'log_payload_style':'varied-structured' if args.varied_logs else 'repeated-character',
               'target_cycle_seconds':2, 'scheduler_enabled':args.mixed_ui or args.priority_api,
               'priority_api':args.priority_api,
               'log_interval_seconds':args.log_interval,
               'independent_logs':args.independent_logs, 'log_drain_seconds':args.drain_seconds if args.independent_logs else 0,
               'trace_http_phases':args.trace_http_phases,
               'client_per_worker':args.client_per_worker,
               'agent_telemetry':args.agent_telemetry,
               'mixed_ui':args.mixed_ui,
               'recovery_load':args.recovery_load,
               'continuous_batches':args.continuous_batches, 'bulk_outage_seconds':args.bulk_outage_seconds,
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
                              cards_per_node=args.cards_per_node)
                identities, recovery_grants, node_groups = seeded if args.recovery_load else (seeded, None, None)
                with engine.begin() as c:
                    c.execute(text("UPDATE evaluation_runs SET lease_expires_at=now()+interval '30 seconds'"))
                def free_port():
                    with socket.socket() as sock:
                        sock.bind(('127.0.0.1', 0))
                        return sock.getsockname()[1]
                ordinary_port = free_port()
                control_port = free_port() if args.split_api else None
                role_ports = {role: free_port() for role in profiles} if args.priority_api else {}
                port = free_port() if args.split_api or args.priority_api else ordinary_port
                with ExitStack() as stack:
                    jwt_secret = uuid.uuid4().hex
                    env = {**os.environ, 'DATABASE_URL':target.render_as_string(hide_password=False), 'JWT_SECRET':jwt_secret,
                           'SCHEDULER_ENABLED':'1' if args.mixed_ui else '0',
                           'DB_BACKPRESSURE_ENABLED':'0' if args.no_backpressure else '1',
                           'DB_ADMISSION_ENABLED':'0' if args.no_admission else '1',
                           'API_DB_PROCESS_COUNT':str(process_count),
                           'DB_CONNECTION_BUDGET':'80', 'DB_BUDGET_DIAGNOSTICS':'1'}
                    if args.mixed_ui:
                        env.update(MAX_ACTIVE_GLOBAL=str(count+10), MAX_ACTIVE_PER_USER=str(count+10))
                    if args.priority_api:
                        env['PRIORITY_API_PEERS'] = json.dumps({role:f'http://127.0.0.1:{p}' for role,p in role_ports.items()})
                        env['PRIORITY_API_POOL_SIZES'] = json.dumps(priority_runtime.DEFAULT_POOLS)
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
                            api_env['API_ROUTE_GROUP'] = role
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
                    else:
                        launch('ordinary', ordinary_port)
                    if args.split_api:
                        launch('control', control_port)
                    if args.split_api or args.priority_api:
                        deploy = Path(__file__).resolve().parents[2] / 'deploy'
                        config = (render_priority_gateway_config(role_ports, port) if args.priority_api else
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
                                                  user_token=jwt.encode({'sub':'u', 'exp':int(time.time())+3600}, jwt_secret,
                                                                        algorithm='HS256') if args.mixed_ui else None)
                    if args.bulk_outage_seconds:
                        def stop_bulk():
                            victim = role_processes['bulk-transfer']
                            victim.terminate()
                            victim.wait(timeout=10)
                        work = with_outage(work, stop_bulk, lambda: launch('bulk-transfer', role_ports['bulk-transfer']),
                                           after=30, duration=args.bulk_outage_seconds)
                    result = asyncio.run(observe(work, engine, container=args.postgres_container))
                    if args.priority_api:
                        topology = {}
                        for role, api_port in role_ports.items():
                            direct = f'http://127.0.0.1:{api_port}'
                            wrong = '/api/v1/batches' if role != 'interaction' else '/api/v1/worker/heartbeat'
                            topology[role] = {'health':httpx.get(direct+'/healthz', trust_env=False).json(),
                                              'ready_status':httpx.get(direct+'/readyz', trust_env=False).status_code,
                                              'wrong_route_status':httpx.post(direct+wrong, trust_env=False).status_code}
                        result['role_topology'] = topology
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
                checkouts = [float(v) for name in api_log_names(count, args.split_api, args.priority_api) for v in
                             re.findall(r'checkout .*?wait_ms=([0-9.]+)', (output/name).read_text())]
                result['initial_checkout_wait_ms'] = {'p95':percentile(checkouts,.95), 'p99':percentile(checkouts,.99)}
                with engine.connect() as c:
                    result['expired_leases'] = c.scalar(text("SELECT count(*) FROM evaluation_runs WHERE lease_expires_at <= now() AND status IN ('STARTING','RUNNING','CANCEL_REQUESTED')"))
                    result['telemetry_stale'] = c.scalar(text("SELECT count(*) FROM gpu_slots WHERE telemetry_at IS NULL OR telemetry_at < now()-interval '45 seconds'"))
                    result['database_max_connections'] = int(c.scalar(text('SHOW max_connections')))
                    result['database_connections_at_end'] = c.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database()"))
                    result['executions'] = c.scalar(text('SELECT count(*) FROM evaluation_runs'))
                    if args.recovery_load:
                        result['recovery_database'] = recovery_database_counts(c)
                result['workers'] = count
                result['device_workers'] = count
                result['node_count'] = (len({group['worker_id'] for group in node_groups.values()})
                                        if node_groups else count)
                result['passed'] = (result['expired_leases'] == 0 and result['telemetry_stale'] == 0 and
                    result['max_heartbeat_gap_seconds'] < 10 and all(v['p99'] < 5 for v in result['latency_seconds'].values()) and
                    acceptable_responses(result['requests_by_kind'], mixed_ui=args.mixed_ui, bulk_outage=args.bulk_outage_seconds))
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
                    result['passed'] = (result['passed'] and ui['batch_id'] is not None
                                        and ui['control_revisions'] == [1, 2]*ui['batch_count'] and not ui['errors']
                                        and ui['retried'] and ui['last_batch_status'] == 'SUCCEEDED'
                                        and len(ui['completed_batches']) == ui['retry_count'] == ui['batch_count']
                                        and [e['status'] for e in ui['executions']] == ['FAILED', 'SUCCEEDED']*ui['batch_count']
                                        and ui['duplicate_results'] == 2*ui['batch_count']
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
                                        and result['requests_by_kind'].get('recovery_control', {}).get('200', 0) >= count-1
                                        and result['successful_latency_seconds'].get('recovery_control', {}).get('p99', 999) < 5
                                        and recovery['release_latency_seconds']['max'] is not None
                                        and recovery['release_latency_seconds']['max'] < 9)
                if args.priority_api:
                    result['passed'] = (result['passed'] and all(row['ready_status'] == 200 and row['wrong_route_status'] == 404
                                         for row in result['role_topology'].values()) and result['role_failure'] == {
                                             'gateway_health_when_bulk_stopped':503, 'bulk_request_when_stopped':502,
                                             'heartbeat_when_bulk_stopped':200, 'gateway_health_after_restart':200})
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
