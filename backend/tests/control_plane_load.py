"""Disposable real HTTP/PostgreSQL control-plane load. Never targets an existing database."""
import argparse
import asyncio
from collections import Counter, defaultdict
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
from backend.app.core import Base, User, Pool, Worker, Slot, Job, Run, ServiceInstance, now


def seed(engine, count):
    Base.metadata.create_all(engine)
    with sessionmaker(engine).begin() as s:
        s.add(User(id='u', username='load-test', password_hash='unused'))
        s.add(Pool(id='p', name='load-test', gpu_model='virtual'))
    identities = []
    for i in range(count):
        identity, token = uuid.uuid4().hex, 'wk_' + uuid.uuid4().hex
        with sessionmaker(engine).begin() as s:
            s.add(Worker(id=identity, node_name='load-'+str(i), token_hash=hashlib.sha256(token.encode()).hexdigest()))
            s.flush()
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
        identities.append((identity, token))
    return identities


def percentile(values, fraction):
    return sorted(values)[min(len(values)-1, int(len(values)*fraction))] if values else None


async def exercise(base, identities, seconds, log_bytes):
    latency, codes, reasons = defaultdict(list), Counter(), Counter()
    successful_latency = defaultdict(list)
    by_kind, busy_by_kind = defaultdict(Counter), defaultdict(Counter)
    latest_heartbeat, latest_sample = {}, {}
    worst_heartbeat_age = 0
    started = time.monotonic()
    async with httpx.AsyncClient(base_url=base, trust_env=False, timeout=5,
                                limits=httpx.Limits(max_connections=len(identities)*2)) as api:
        async def post(kind, identity, token, path, body):
            begin = time.monotonic()
            try:
                reply = await api.post('/api/v1/'+path, json=body, headers={'Authorization': 'Bearer '+token})
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
                reply = await post('logs', identity, token, f'worker/executions/{identity}/logs-v2', {'sequence':seq, 'content':'x'*log_bytes})
                if reply is not None and reply.status_code == 200:
                    seq = reply.json()['next_sequence']
                await post('artifact', identity, token, f'worker/executions/{identity}/artifacts', {'name':'load.json','content_base64':'e30='})
                await post('claim', identity, token, f'worker/slots/{identity}/claim', {'known_execution_ids':[identity]})
                await asyncio.sleep(max(.05, 2-(time.monotonic()-cycle)) + random.uniform(0,.1))
        await asyncio.gather(*(worker(*item) for item in identities))
    end = time.monotonic()
    return {'requests': dict(codes), 'busy_reasons': dict(reasons),
            'requests_by_kind': {k:dict(v) for k,v in by_kind.items()},
            'busy_by_kind': {k:dict(v) for k,v in busy_by_kind.items()},
            'successful_latency_seconds': {k:{'p95':percentile(v,.95), 'p99':percentile(v,.99), 'max':max(v)} for k,v in successful_latency.items()},
            'latency_seconds': {k: {'p95':percentile(v,.95), 'p99':percentile(v,.99), 'max':max(v)} for k,v in latency.items()},
            'max_heartbeat_gap_seconds': max(worst_heartbeat_age, max(end-v for v in latest_heartbeat.values())),
            'max_final_sample_age_seconds': max(end-v for v in latest_sample.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--grades', default='50,100,200')
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--log-bytes', type=int, default=1024)
    parser.add_argument('--output', required=True)
    parser.add_argument('--ordinary-pool', type=int, default=10)
    parser.add_argument('--control-pool', type=int, default=5)
    args = parser.parse_args()
    grades = [int(x) for x in args.grades.split(',')]
    if min(grades) < 1 or args.seconds < 5 or not 1 <= args.log_bytes <= 16384 or min(args.ordinary_pool, args.control_pool) < 1 or args.ordinary_pool + args.control_pool + 5 > 80:
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
               'scope':'real HTTP/PostgreSQL, simulated running services; excludes scheduler allocation, Agent and real business processes', 'grades':[]}
    try:
        for count in grades:
            database = 'control_load_'+uuid.uuid4().hex
            with admin.connect() as c:c.execute(text('CREATE DATABASE '+database))
            target = url.set(database=database)
            engine = create_engine(target)
            process = None
            try:
                identities = seed(engine, count)
                with engine.begin() as c:
                    c.execute(text("UPDATE evaluation_runs SET lease_expires_at=now()+interval '30 seconds'"))
                with socket.socket() as sock:
                    sock.bind(('127.0.0.1',0));port = sock.getsockname()[1]
                with (output/f'api-{count}.log').open('w') as log:
                    env = {**os.environ, 'DATABASE_URL':target.render_as_string(hide_password=False), 'JWT_SECRET':uuid.uuid4().hex,
                           'SCHEDULER_ENABLED':'0', 'DB_BACKPRESSURE_ENABLED':'1', 'DB_POOL_SIZE':str(args.ordinary_pool), 'DB_CONTROL_POOL_SIZE':str(args.control_pool),
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
                    result = asyncio.run(exercise(base, identities, args.seconds, args.log_bytes))
                checkouts = [float(v) for v in re.findall(r'checkout .*?wait_ms=([0-9.]+)', (output/f'api-{count}.log').read_text())]
                result['initial_checkout_wait_ms'] = {'p95':percentile(checkouts,.95), 'p99':percentile(checkouts,.99)}
                with engine.connect() as c:
                    result['expired_leases'] = c.scalar(text('SELECT count(*) FROM evaluation_runs WHERE lease_expires_at <= now()'))
                    result['telemetry_stale'] = c.scalar(text("SELECT count(*) FROM gpu_slots WHERE telemetry_at IS NULL OR telemetry_at < now()-interval '45 seconds'"))
                result['workers'] = result['executions'] = count
                result['passed'] = (result['expired_leases'] == 0 and result['telemetry_stale'] == 0 and
                    result['max_heartbeat_gap_seconds'] < 10 and all(v['p99'] < 5 for v in result['latency_seconds'].values()) and
                    not any(k not in ('200','503') for k in result['requests']))
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
