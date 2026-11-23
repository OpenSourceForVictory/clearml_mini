"""Disposable real HTTP/PostgreSQL control-plane load. Never targets an existing database."""
import argparse
import asyncio
from collections import Counter, defaultdict, deque
from contextlib import AsyncExitStack
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
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from worker.backpressure import parse_retry_after
from backend.app.core import Base, User, Pool, Worker, AgentObservation, Slot, Job, Run, ServiceInstance, now


def seed(engine, count, *, agent_telemetry=False):
    Base.metadata.create_all(engine)
    with sessionmaker(engine).begin() as s:
        s.add(User(id='u', username='load-test', password_hash='unused'))
        s.add(Pool(id='p', name='load-test', gpu_model='virtual'))
    identities = []
    for i in range(count):
        identity, token = uuid.uuid4().hex, 'wk_' + uuid.uuid4().hex
        agent_session = uuid.uuid4().hex
        with sessionmaker(engine).begin() as s:
            connection = {'agent': {'session': agent_session, 'boot_id': uuid.uuid4().hex,
                                    'resource_protocol': 1, 'revision': 0,
                                    'desired': {'heartbeat_seconds': 5, 'sample_seconds': 15, 'programs': 'running'}}} if agent_telemetry else {}
            s.add(Worker(id=identity, node_name='load-'+str(i), token_hash=hashlib.sha256(token.encode()).hexdigest(),
                         connection=connection))
            s.flush()
            if agent_telemetry:
                s.add(AgentObservation(worker_id=identity, session=agent_session, payload={}))
            s.add(Slot(id=identity, worker_id=identity, pool_id='p', node_name='load-'+str(i), queue_name='load-'+identity, gpu_index=0,
                       gpu_uuid='mock:'+identity, gpu_model='virtual', vram_mb=1024))
            s.flush()
            s.add(ServiceInstance(id=identity, name='load', worker_id=identity, slot_id=identity,
                                  address='127.0.0.1', port=19000, template_snapshot={}, current_job_id=identity))
            s.flush()
            s.add(Job(id=identity, owner_id='u', pool_id='p', title='load', benchmark='mock', model_ref='mock',
                      episodes=1, status='RUNNING', service_instance_id=identity))
            s.flush()
            s.add(Run(id=identity, job_id=identity, slot_id=identity, execution_id=identity, attempt=1,
                      status='RUNNING', lease_expires_at=now()+timedelta(seconds=30)))
        identities.append((identity, token, agent_session) if agent_telemetry else (identity, token))
    return identities


def percentile(values, fraction):
    return sorted(values)[min(len(values)-1, int(len(values)*fraction))] if values else None


async def exercise(base, identities, seconds, log_bytes, *, independent_logs=False, drain_seconds=0,
                   trace_http_phases=False, client_per_worker=False, agent_telemetry=False,
                   progress_path=None, log_interval=2):
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
    started = time.monotonic()
    async with AsyncExitStack() as stack:
        if client_per_worker:
            clients = {identity: await stack.enter_async_context(httpx.AsyncClient(
                base_url=base, trust_env=False, timeout=5,
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=2)))
                for identity, *_ in identities}
        else:
            shared = await stack.enter_async_context(httpx.AsyncClient(
                base_url=base, trust_env=False, timeout=5,
                limits=httpx.Limits(max_connections=len(identities)*2,
                                    max_keepalive_connections=len(identities)*2)))
            clients = {identity: shared for identity, *_ in identities}
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
                                   {'sequence': sequence, 'content': 'x' * log_bytes, 'gap': gap})
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
                reply = await post('agent', identity, token, 'agent/heartbeat', {
                    'session': session, 'sequence': sequence, 'applied_revision': 0,
                    'config_status': 'applied',
                    'samples': {'gpu': {'status': 'ok', 'sampled_at': sampled,
                                        'values': {'gpu_0_uuid': 'mock:'+identity, 'gpu_0_total_mb': 1024,
                                                   'gpu_0_free_mb': 1024, 'gpu_0_utilization': 0}},
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
                reply = await post('heartbeat', identity, token, 'worker/heartbeat', {'slot_id':identity, 'execution_id':identity,
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
                await post('health', identity, token, f'worker/executions/{identity}/service-health', {'ready':True})
                if not independent_logs:
                    reply = await post('logs', identity, token, f'worker/executions/{identity}/logs-v2',
                                       {'sequence':seq, 'content':'x'*log_bytes})
                    if reply is not None and reply.status_code == 200:
                        seq = reply.json()['next_sequence']
                await post('artifact', identity, token, f'worker/executions/{identity}/artifacts', {'name':'load.json','content_base64':'e30='})
                await post('claim', identity, token, f'worker/slots/{identity}/claim', {'known_execution_ids':[identity]})
                await asyncio.sleep(max(.05, 2-(time.monotonic()-cycle)) + random.uniform(0,.1))
        log_tasks = ([asyncio.create_task(task) for item in identities for task in
                      (produce_logs(item[0]), send_logs(*item[:2]))] if independent_logs else [])
        agent_tasks = [asyncio.create_task(agent(*item)) for item in identities] if agent_telemetry else []
        loop_task = asyncio.create_task(sample_loop_lag())
        progress_task = asyncio.create_task(sample_progress()) if progress_path else None
        await asyncio.gather(*(worker(*item[:2]) for item in identities))
        end = time.monotonic()
        await loop_task
        if progress_task:
            await progress_task
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
            'independent_logs': independent_logs,
            'log_generation': {**dict(log_stats), 'max_pending_chunks_per_worker': max_log_pending,
                               'final_pending_chunks': sum(len(queue) for queue in log_queues.values()),
                               'max_pending_age_seconds': max_log_age} if independent_logs else None}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--grades', default='50,100,200')
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--log-bytes', type=int, default=1024)
    parser.add_argument('--log-interval', type=float, default=2)
    parser.add_argument('--output', required=True)
    parser.add_argument('--ordinary-pool', type=int, default=10)
    parser.add_argument('--control-pool', type=int, default=5)
    parser.add_argument('--independent-logs', action='store_true')
    parser.add_argument('--drain-seconds', type=int, default=5)
    parser.add_argument('--trace-http-phases', action='store_true')
    parser.add_argument('--client-per-worker', action='store_true')
    parser.add_argument('--agent-telemetry', action='store_true')
    parser.add_argument('--progress-every-30s', action='store_true')
    args = parser.parse_args()
    grades = [int(x) for x in args.grades.split(',')]
    if min(grades) < 1 or args.seconds < 5 or not 1 <= args.log_bytes <= 16384 or not .1 <= args.log_interval <= 60 or args.drain_seconds < 0 or min(args.ordinary_pool, args.control_pool) < 1 or args.ordinary_pool + args.control_pool + 5 > 80:
        parser.error('positive grades, seconds >= 5 and log bytes 1..16384 required')
    url = make_url(os.environ['LOG_TEST_DATABASE_URL'])
    if url.get_backend_name() != 'postgresql':
        parser.error('requires disposable PostgreSQL admin URL')
    output = Path(args.output);output.mkdir(parents=True, exist_ok=True)
    admin = create_engine(url, isolation_level='AUTOCOMMIT')
    summary = {'hardware': {'machine':platform.machine(), 'cpu_count':os.cpu_count()},
               'api_processes':1, 'pool_ordinary':args.ordinary_pool, 'pool_control':args.control_pool, 'pool_observation':2, 'pool_result':2, 'pool_scheduler':1, 'cards_per_node':1,
               'seconds_per_grade':args.seconds, 'log_bytes_per_worker_per_cycle':args.log_bytes,
               'target_cycle_seconds':2, 'scheduler_enabled':False,
               'log_interval_seconds':args.log_interval,
               'independent_logs':args.independent_logs, 'log_drain_seconds':args.drain_seconds if args.independent_logs else 0,
               'trace_http_phases':args.trace_http_phases,
               'client_per_worker':args.client_per_worker,
               'agent_telemetry':args.agent_telemetry,
               'scope':('real HTTP/PostgreSQL, simulated running services and Agent telemetry; '
                        'excludes scheduler allocation and real business processes' if args.agent_telemetry else
                        'real HTTP/PostgreSQL, simulated running services; excludes scheduler allocation, Agent and real business processes'),
               'grades':[]}
    try:
        for count in grades:
            database = 'control_load_'+uuid.uuid4().hex
            with admin.connect() as c:c.execute(text('CREATE DATABASE '+database))
            target = url.set(database=database)
            engine = create_engine(target)
            process = None
            try:
                identities = seed(engine, count, agent_telemetry=args.agent_telemetry)
                with engine.begin() as c:
                    c.execute(text("UPDATE evaluation_runs SET lease_expires_at=now()+interval '30 seconds'"))
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1',0));port = sock.getsockname()[1]
                with (output/f'api-{count}.log').open('w') as log:
                    env = {**os.environ, 'DATABASE_URL':target.render_as_string(hide_password=False), 'JWT_SECRET':uuid.uuid4().hex,
                           'SCHEDULER_ENABLED':'0', 'DB_BACKPRESSURE_ENABLED':'1', 'DB_POOL_SIZE':str(args.ordinary_pool), 'DB_CONTROL_POOL_SIZE':str(args.control_pool),
                           'DB_OBSERVATION_POOL_SIZE':'2', 'DB_RESULT_POOL_SIZE':'2', 'DB_SCHEDULER_POOL_SIZE':'1',
                           'API_DB_PROCESS_COUNT':'1', 'DB_CONNECTION_BUDGET':'80', 'DB_BUDGET_DIAGNOSTICS':'1'}
                    process = subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.main:app','--host','127.0.0.1',
                                                '--port',str(port),'--no-access-log'], env=env, stdout=log, stderr=log)
                    base = f'http://127.0.0.1:{port}'
                    for _ in range(100):
                        if process.poll() is not None:raise RuntimeError('isolated API exited; inspect API log')
                        try:
                            if httpx.get(base+'/openapi.json',timeout=1,trust_env=False).status_code == 200:break
                        except httpx.TransportError:pass
                        time.sleep(.1)
                    else:raise RuntimeError('isolated API did not become ready')
                    result = asyncio.run(exercise(base, identities, args.seconds, args.log_bytes,
                                                  independent_logs=args.independent_logs,
                                                  drain_seconds=args.drain_seconds if args.independent_logs else 0,
                                                  trace_http_phases=args.trace_http_phases,
                                                  client_per_worker=args.client_per_worker,
                                                  agent_telemetry=args.agent_telemetry,
                                                  progress_path=output/f'progress-{count}.jsonl' if args.progress_every_30s else None,
                                                  log_interval=args.log_interval))
                checkouts = [float(v) for v in re.findall(r'checkout .*?wait_ms=([0-9.]+)', (output/f'api-{count}.log').read_text())]
                result['initial_checkout_wait_ms'] = {'p95':percentile(checkouts,.95), 'p99':percentile(checkouts,.99)}
                with engine.connect() as c:
                    result['expired_leases'] = c.scalar(text('SELECT count(*) FROM evaluation_runs WHERE lease_expires_at <= now()'))
                    result['telemetry_stale'] = c.scalar(text("SELECT count(*) FROM gpu_slots WHERE telemetry_at IS NULL OR telemetry_at < now()-interval '45 seconds'"))
                result['workers'] = result['executions'] = count
                result['passed'] = (result['expired_leases'] == 0 and result['telemetry_stale'] == 0 and
                    result['max_heartbeat_gap_seconds'] < 10 and all(v['p99'] < 5 for v in result['latency_seconds'].values()) and
                    not any(k not in ('200','503') for k in result['requests']))
                if args.independent_logs:
                    logs = result['log_generation']
                    result['passed'] = (result['passed'] and logs['generated'] == logs.get('accepted', 0)
                                        and logs['final_pending_chunks'] == 0 and not logs.get('dropped', 0)
                                        and not logs.get('permanent_error', 0) and not logs.get('unexpected_ack', 0))
                if args.agent_telemetry:
                    result['passed'] = (result['passed'] and result['max_final_agent_age_seconds'] < 10
                                        and result['requests_by_kind'].get('agent', {}).get('200', 0) >= count)
                summary['grades'].append(result)
                (output/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n')
                print(json.dumps({'workers':count,'passed':result['passed'],'requests':result['requests']}),flush=True)
                if not result['passed']:break
            finally:
                if process is not None:
                    process.terminate()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                engine.dispose()
                with admin.connect() as c:c.execute(text('DROP DATABASE '+database+' WITH (FORCE)'))
    finally:admin.dispose()


if __name__ == '__main__':main()
