"""Real Sender/HTTP/four-role PostgreSQL burst; synthetic executions, not models."""
import json
from collections import Counter
import os
import socket
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack

import httpx
import jwt
import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from backend.tests.test_execution_logs_postgres import pg
from backend.tests.control_plane_load import seed
from backend.app.core import EpisodeRecord, Job, RunLogChunk
from backend.app.priority_runtime import ROLE_POOLS, DEFAULT_POOLS
from runner.log_capture import Ring
from worker.client import Client
from worker.backpressure import APIError
from worker.log_stream import Sender


@pytest.mark.parametrize('lose_ack', [False, True])
def test_100_senders_reconnect_with_heartbeats_results_and_page_reads(pg, tmp_path, lose_ack):
    identities = seed(pg[0], 100)
    with Session(pg[0]) as session:
        for job in session.scalars(select(Job)):
            job.client_spec = {'seed':0, 'service_mode':'none', 'template':{'episode_reporting':True}}
        session.execute(text("UPDATE evaluation_runs SET lease_expires_at=now()+interval '5 minutes'"))
        session.commit()
    ports = {}
    for role in ROLE_POOLS:
        with socket.socket() as sock:
            sock.bind(('127.0.0.1', 0)); ports[role] = sock.getsockname()[1]
    peers = {role:f'http://127.0.0.1:{port}' for role,port in ports.items()}
    secret = 'isolated-log-reconnect-test-only'
    processes=[]; senders=[]; release=threading.Event(); calls=[]; rejections=[]; lock=threading.Lock()
    metrics={'lost_first_ack':lose_ack, 'workers':100, 'roles':4, 'pool_sizes':DEFAULT_POOLS, 'heartbeat_seconds':[],
             'page_seconds':[], 'scope':'real Sender/HTTP/PostgreSQL; synthetic seeded executions; no gateway or real business program'}
    try:
        with ExitStack() as stack:
            for role in ROLE_POOLS:
                env={**os.environ, 'DATABASE_URL':pg[1], 'JWT_SECRET':secret,
                     'DB_BACKPRESSURE_ENABLED':'1', 'DB_ADMISSION_ENABLED':'1',
                     'API_ROUTE_GROUP':role, 'API_DB_PROCESS_COUNT':'4',
                     'DB_CONNECTION_BUDGET':'80', 'PRIORITY_API_PEERS':json.dumps(peers),
                     'PRIORITY_API_POOL_SIZES':json.dumps(DEFAULT_POOLS),
                     'SCHEDULER_ENABLED':'1' if role=='worker-data' else '0'}
                log=stack.enter_context((tmp_path/(role+'.log')).open('w'))
                processes.append(subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.main:app',
                    '--host','127.0.0.1','--port',str(ports[role]),'--no-access-log'],env=env,stdout=log,stderr=log))
            http=stack.enter_context(httpx.Client(trust_env=False,timeout=5))
            until=time.monotonic()+30
            for base in peers.values():
                while True:
                    assert all(p.poll() is None for p in processes), 'test API failed to start'
                    try:
                        if http.get(base+'/healthz').status_code==200:break
                    except httpx.TransportError:pass
                    assert time.monotonic()<until, 'test API startup timeout'
                    time.sleep(.1)
            class Transport:
                def __init__(self, token):
                    self.lost=False
                    self.client=Client({'api_url':peers['bulk-transfer'],'token':token})
                def post(self, path, body):
                    assert release.wait(30)
                    try:
                        response=self.client.post(path,body)
                    except APIError as exc:
                        with lock:rejections.append(exc.status)
                        raise
                    with lock:calls.append((path,dict(body)))
                    if lose_ack and not self.lost:
                        self.lost=True
                        raise ConnectionError("injected response loss after database commit")
                    return response
            for identity,token in identities:
                root=tmp_path/identity
                ring=Ring(root/'logs')
                # A request already sent before disconnection remains immutable.
                journal=root/'sender.json'
                journal.write_text(json.dumps({'execution':identity,'offset':0,'sequence':0,
                    'pending':{'sequence':0,'content':'old pending','stream':'stdout','gap':False},
                    'pending_offset':1}))
                for index in range(64):ring.write('stdout',f'{index:04d}:'+'x'*2043)
                senders.append(Sender(Transport(token),identity,root/'logs',journal))
            def control(item):
                identity,token=item
                client=Client({'api_url':peers['execution-control'],'token':token})
                began=time.monotonic()
                reply=client.post('worker/heartbeat',{'slot_id':identity,'execution_id':identity,'started':True})
                assert not reply.get('cancel'), reply
                return time.monotonic()-began
            def results(item):
                identity,token=item
                client=Client({'api_url':peers['worker-data'],'token':token})
                path='worker/executions/'+identity+'/episodes'
                start={'episode_id':1,'seed':0,'status':'STARTED'}
                end={'episode_id':1,'seed':0,'status':'COMPLETED','success':True,
                     'metrics':{},'runtime':1,'artifacts':[]}
                for event in (start,end,end):client.post(path,event)
            began=time.monotonic();release.set()
            user={'Authorization':'Bearer '+jwt.encode({'sub':'u','exp':int(time.time())+300},secret,algorithm='HS256')}
            with ThreadPoolExecutor(16) as controls, ThreadPoolExecutor(8) as result_pool:
                result_futures=[result_pool.submit(results,item) for item in identities]
                deadline=time.monotonic()+30
                while True:
                    metrics['heartbeat_seconds'].extend(controls.map(control,identities))
                    page_start=time.monotonic()
                    response=http.get(peers['interaction']+'/api/v1/jobs',headers=user)
                    assert response.status_code==200,response.text
                    metrics['page_seconds'].append(time.monotonic()-page_start)
                    if all(s.state['offset']==64 and s.state['pending'] is None for s in senders):break
                    assert time.monotonic()<deadline,'log backlog did not drain'
                    time.sleep(.5)
                for future in result_futures:future.result(timeout=10)
            metrics['elapsed_seconds']=time.monotonic()-began
            with Session(pg[0]) as session:
                rows=list(session.scalars(select(RunLogChunk)))
                episodes=list(session.scalars(select(EpisodeRecord)))
                assert len(episodes)==100 and all(r.result['success'] for r in episodes)
                for identity,_ in identities:
                    chunks=sorted((r for r in rows if r.run_id==identity),key=lambda r:r.sequence)
                    assert len(chunks)==6
                    assert chunks[0].content=='old pending'
                    assert chunks[1].gap
                    assert ''.join(r.content for r in chunks[1:])==''.join(f'{i:04d}:'+'x'*2043 for i in range(32,64))
                metrics.update(log_rejections=dict(Counter(rejections)),log_requests=len(calls),database_log_chunks=len(rows),episode_results=len(episodes))
                assert len(calls)==(700 if lose_ack else 600)
            assert max(metrics['heartbeat_seconds'])<5
            assert max(metrics['page_seconds'])<5
            print('LOG_RECONNECT_METRICS '+json.dumps(metrics))
    finally:
        release.set()
        for sender in senders:sender.close()
        for sender in senders:sender.thread.join(timeout=6)
        for process in processes:
            process.terminate()
        for process in processes:
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait()
