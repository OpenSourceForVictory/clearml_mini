"""Opt-in new Worker -> previous real API compatibility, with real local processes."""
import hashlib
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from datetime import timedelta

import httpx
import pytest
from sqlalchemy.orm import sessionmaker
from backend.app.core import Base, User, Pool, Worker as WorkerRecord, Slot, Job, Run, now
from backend.tests.test_execution_logs_postgres import pg
from backend.tests.test_host_backend import worker as host_fixture
from worker.client import Worker

pytestmark=pytest.mark.skipif(not os.getenv('BACKPRESSURE_PREVIOUS_API'),reason='requires previous API checkout, PostgreSQL and local host/Docker')


@pytest.mark.parametrize('mode',['host','docker'])
def test_new_worker_with_previous_api(pg,tmp_path,mode):
    root=Path(os.environ['BACKPRESSURE_PREVIOUS_API']).resolve()
    assert (root/'backend/app/main.py').is_file()
    Base.metadata.create_all(pg[0])
    fixture=host_fixture(tmp_path,['python3','-c',"print('previous API compatibility')"])
    task=fixture.state['task'];identity=task['execution_id']
    task.update(status='CLAIMED',lease_seconds=30,benchmark='cs-client',cpus=0,ram_gb=0)
    task['job_id']=identity
    task['client_spec']['service_mode']='none'
    template=task['client_spec']['template']
    template.update(task_type='command',integration_mode='direct',execution_mode=mode)
    image=os.getenv('BACKPRESSURE_TEST_IMAGE','mini-eval-runner:dev')
    if mode=='docker':
        task['image']=template['image']=image
        template['runtime']={'command_mode':'exec','working_directory':'/tmp','assets':{}}
    token='wk_isolated_compatibility'
    factory=sessionmaker(pg[0])
    with factory.begin() as s:
        s.add(User(id='u',username='u',password_hash='unused'))
        s.add(Pool(id='p',name='p',gpu_model='virtual'))
        s.add(WorkerRecord(id='w',node_name='compatibility',token_hash=hashlib.sha256(token.encode()).hexdigest()))
        s.flush()
        s.add(Slot(id='s',worker_id='w',pool_id='p',node_name='compatibility',queue_name='compatibility',gpu_index=0,gpu_uuid='mock:compatibility',gpu_model='virtual'))
        s.flush()
        s.add(Job(id=identity,owner_id='u',pool_id='p',title='compatibility',benchmark='cs-client',model_ref='generic',
                  episodes=1,status='RUNNING',client_spec=task['client_spec']))
        s.flush()
        s.add(Run(id=identity,job_id=identity,slot_id='s',execution_id=identity,attempt=1,status='RUNNING',lease_expires_at=now()+timedelta(seconds=30)))
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    env={**os.environ,'DATABASE_URL':pg[1],'JWT_SECRET':'isolated-compatibility-test', 'SCHEDULER_ENABLED':'0',
         'PYTHONPATH':str(root),'DB_BACKPRESSURE_ENABLED':'0'}
    with (tmp_path/'previous-api.log').open('w') as log:
        process=subprocess.Popen([sys.executable,'-m','uvicorn','backend.app.main:app','--host','127.0.0.1','--port',str(port),
                                  '--no-access-log'],cwd=root,env=env,stdout=log,stderr=log)
        config={**fixture.config,'api_url':f'http://127.0.0.1:{port}','token':token,'runner_image':image,'client_images':[image],
                '_host_approval':fixture.state['host_approval']}
        worker=Worker(config,tmp_path/'execution-state',snapshot={'revision':0,'config':fixture.config})
        worker.slot_id='s'
        try:
            for _ in range(100):
                assert process.poll() is None, (tmp_path/'previous-api.log').read_text()
                try:
                    if httpx.get(config['api_url']+'/openapi.json',trust_env=False,timeout=1).status_code==200:break
                except httpx.TransportError:pass
                time.sleep(.1)
            else:pytest.fail('previous API did not start')
            worker.execute(task)
            assert worker.state is None
            with factory() as s:
                run=s.get(Run,identity)
                assert run.status=='SUCCEEDED', run.error
                assert run.exit_code==0
            assert not worker.inspect(identity)
        finally:
            try:worker.stop_container(identity);worker.remove(identity)
            finally:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
