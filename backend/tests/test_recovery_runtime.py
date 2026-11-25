import json
import os
import time
import uuid
from types import SimpleNamespace

import pytest

from worker.recovery_runtime import RecoveryRuntime
from worker.recovery_supervisor import RecoverySupervisor
from worker.host_backend import HostBackend
from worker.client import Worker, docker
from backend.tests.test_host_backend import worker as host_worker


@pytest.mark.skipif(os.getenv('RECOVERY_RUNTIME_TEST') != '1',
                    reason='real host/Docker multi-execution isolation opt-in')
@pytest.mark.parametrize('mode', ['host', 'docker'])
def test_real_same_name_services_and_clients_keep_distinct_runtime_identity(tmp_path, mode):
    """Two same-name services and two clients on one node never share control."""
    entries = []
    script = 'import time; print("ready", flush=True); time.sleep(60)'
    try:
        for index, role in enumerate(('service', 'service', 'client', 'client')):
            root = tmp_path / f'slot{index}'
            root.mkdir()
            (root / 'business.py').write_text(script)
            worker = host_worker(root, ['python3', 'business.py'], timeout=0)
            worker.host_mode = lambda value=mode: value == 'host'
            task = worker.state['task']
            task['service_instance_id'] = str(uuid.uuid4()) if role == 'service' else None
            task['service_name'] = 'shared-display-name' if role == 'service' else None
            task['device_index'] = index
            execution = task['execution_id']
            backend = HostBackend(worker)
            name = 'mini-eval-' + execution
            if mode == 'host':
                backend.create(task)
                path = backend.root / 'launch.json'
                launch = json.loads(path.read_text())
                launch['command'] = ['/usr/bin/python3', str(root / 'business.py')]
                path.write_text(json.dumps(launch))
                backend.start(task)
            else:
                docker('run', '--detach', '--name', name,
                       '--label', 'mini-eval.execution=' + execution,
                       '--label', 'mini-eval.service_name=shared-display-name',
                       '--entrypoint', 'python',
                       os.getenv('RECOVERY_RUNTIME_IMAGE', 'mini-eval-api:recovery-foundations-20261125'),
                       '-c', script)
                worker.name = lambda item: 'mini-eval-' + item
                worker.inspect = lambda item, owner=worker: Worker.inspect(owner, item)
            runtime = RecoveryRuntime(worker, execution)
            entries.append((role, task['service_instance_id'], runtime, backend, name))

        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            observed = [entry[2].inspect() for entry in entries]
            if all(value['state'] == 'running' for value in observed):
                break
            time.sleep(.05)
        assert all(value['state'] == 'running' for value in observed)
        assert len({value['identity']['runtime_id'] for value in observed}) == 4
        assert entries[0][1] != entries[1][1]
        for entry, value in zip(entries, observed):
            entry[2].worker.state['recovery'] = {
                'generation': 'isolated-multicard-test', 'identity': value['identity']}

        entries[1][2].stop(observed[1]['identity'])
        assert entries[1][2].inspect()['state'] == 'exited'
        assert all(entries[i][2].inspect()['state'] == 'running' for i in (0, 2, 3))
    finally:
        for _, _, runtime, backend, name in reversed(entries):
            try:
                current = runtime.inspect()
                if current['state'] == 'running':
                    runtime.stop(current['identity'])
            finally:
                if mode == 'host':
                    backend.remove(runtime.execution_id)
                else:
                    docker('rm', '--force', name, check=False)


@pytest.mark.parametrize('host',[True,False])
def test_runtime_binding_and_stop_uses_immutable_id(tmp_path,monkeypatch,host):
    w=host_worker(tmp_path,['python3','business.py']);w.host_mode=lambda:host
    data={'State':{'Running':True,'ExitCode':0},'Id':'a'*64,'InvocationID':'b'*32}
    calls=[]
    w.inspect=lambda e:data
    monkeypatch.setattr(HostBackend,'inspect',lambda *a,**kw:data)
    def stopped(value):
        calls.append(value);data['State']['Running']=False
    monkeypatch.setattr(HostBackend,'stop',lambda *a:stopped('host-stop'))
    monkeypatch.setattr('worker.client.docker',lambda *a,**kw:stopped(a))
    runtime=RecoveryRuntime(w,w.state['task']['execution_id'])
    bound=runtime.inspect()['identity']
    assert bound['kind']==('host' if host else 'docker')
    with pytest.raises(RuntimeError,match='different'):runtime.stop({**bound,'runtime_id':'c'*len(bound['runtime_id'])})
    assert calls==[]
    runtime.stop(bound)
    assert calls==(['host-stop'] if host else [('stop','--time','10','a'*64)])
    data['State']['Running']=False
    assert runtime.inspect()['state']=='exited'
    runtime.stop(bound)
    assert len(calls)==1
    data['State']['Recovered']=True
    assert runtime.inspect()['state']=='missing'
    runtime.stop(bound)
    assert len(calls)==1
    with pytest.raises(ValueError):RecoveryRuntime(w,'other')


@pytest.mark.skipif(os.getenv('RECOVERY_RUNTIME_TEST')!='1',reason='real host/Docker recovery supervision opt-in')
@pytest.mark.parametrize('mode',['host','docker'])
@pytest.mark.parametrize('tolerance',[None,60])
def test_real_process_continues_across_failed_control_then_stops_or_finishes(tmp_path,mode,tolerance):
    """Real runtime + local supervisor, injected HTTP outage/monotonic time.

    This does not advertise recovery or start a platform evaluation batch.
    """
    script='import time\nprint("started",flush=True)\ntime.sleep(2)\nprint("completed",flush=True)\n'
    (tmp_path/'business.py').write_text(script)
    w=host_worker(tmp_path,['python3','business.py'],timeout=0)
    w.host_mode=lambda:mode=='host'
    task=w.state['task'];execution=task['execution_id'];host=HostBackend(w)
    name='mini-eval-'+execution
    supervisor=None
    if mode=='host':
        host.create(task)
        launch=json.loads((host.root/'launch.json').read_text())
        launch['command']=['/usr/bin/python3',str(tmp_path/'business.py')]
        (host.root/'launch.json').write_text(json.dumps(launch))
        host.start(task)
    else:
        docker('run','--detach','--name',name,'--label','mini-eval.execution='+execution,
               '--entrypoint','python',os.environ.get('RECOVERY_RUNTIME_IMAGE','mini-eval-api:recovery-foundations-20261125'),'-c',script)
        w.name=lambda e:'mini-eval-'+e
        w.inspect=lambda e:Worker.inspect(w,e)
    try:
        runtime=RecoveryRuntime(w,execution)
        end=time.monotonic()+5
        while time.monotonic()<end:
            observed=runtime.inspect()
            if observed['state']=='running':break
            time.sleep(.05)
        assert observed['state']=='running'
        bound=observed['identity']
        supervisor=RecoverySupervisor(tmp_path/'supervision',execution_id=execution,generation='test',
            process_identity=bound,tolerance=tolerance,started_at=0,requested_at=0)
        def disconnected(*args):raise ConnectionError('injected control outage')
        with pytest.raises(ConnectionError):supervisor.exchange(SimpleNamespace(post=disconnected),clock=lambda:1)
        # Past the old 30-second heartbeat lease, but inside 60-second tolerance.
        assert supervisor.tick(runtime.inspect,runtime.stop,at=35)=='track'
        assert runtime.inspect()['identity']==bound and runtime.inspect()['state']=='running'
        supervisor.close()
        supervisor=RecoverySupervisor(tmp_path/'supervision',execution_id=execution,generation='test',
            process_identity=bound,tolerance=tolerance,started_at=35,requested_at=35)
        assert supervisor.snapshot()['started_at']==0
        if tolerance:
            assert supervisor.tick(runtime.inspect,runtime.stop,at=60)=='stop'
        end=time.monotonic()+8
        while time.monotonic()<end:
            observed=runtime.inspect()
            if observed['state']!='running':break
            time.sleep(.05)
        assert observed['state']!='running'
        assert supervisor.tick(runtime.inspect,runtime.stop,at=61)=='retain'
        if tolerance is None:
            assert observed['state']=='exited' and observed['exit_code']==0
            assert supervisor.snapshot()['exit_code']==0
        else:assert supervisor.snapshot()['stop_reason']=='disconnection_timeout'
    finally:
        if supervisor:supervisor.close()
        if mode=='host':host.remove(execution)
        else:docker('rm','--force',name,check=False)


@pytest.mark.skipif(os.getenv('RECOVERY_RUNTIME_TEST')!='1',reason='real host/Docker API reconciliation opt-in')
@pytest.mark.parametrize('mode,cancel,stop_failure',
    [(mode, cancel, False) for mode in ('host', 'docker') for cancel in (False, True)] +
    [(mode, True, True) for mode in ('host', 'docker')])
def test_real_runtime_reconciles_original_identity_after_lease_expiry(
        tmp_path,monkeypatch,mode,cancel,stop_failure):
    from datetime import timedelta
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.pool import StaticPool
    from backend.app import recovery_control, recovery_records, scheduling
    from backend.app.core import Run, Job, now
    from backend.tests.test_execution_logs_postgres import seed

    w=host_worker(tmp_path,['python3','business.py'],timeout=0)
    w.host_mode=lambda:mode=='host'
    task=w.state['task'];execution=task['execution_id'];name='mini-eval-'+execution
    script='import time,signal,sys;signal.signal(signal.SIGTERM,lambda *a:sys.exit(0));print("started",flush=True);time.sleep('+str(30 if cancel else 3)+');print("completed",flush=True)'
    (tmp_path/'business.py').write_text(script)
    host=HostBackend(w)
    engine=create_engine('sqlite://',connect_args={'check_same_thread':False},poolclass=StaticPool)
    factory=seed(engine);at=now()
    with factory() as db:
        run=db.get(Run,'r');run.execution_id=execution;run.status='STARTING';run.enqueued_at=at
        db.get(Job,'j').client_spec={'timeout_seconds':0,'template':{'task_type':'command'}}
        generation=recovery_records.grant(db,run,'w',60).generation;db.commit()
    def database():
        with factory() as db:yield db
    app=FastAPI();recovery_control.install(app,database,lambda:SimpleNamespace(id='w'))
    from backend.app import recovery_finish
    from backend.app.execution import Finish,RecoveryProof
    @app.post('/api/v1/worker/executions/{execution_id}/complete')
    def complete(execution_id:str,body:Finish):
        with factory() as db:
            reply=recovery_finish.finish(db,worker_id='w',execution_id=execution_id,body=body);db.commit();return reply
    @app.post('/api/v1/worker/executions/{execution_id}/cleanup')
    def cleanup(execution_id:str,body:RecoveryProof):
        with factory() as db:
            reply=recovery_finish.cleanup(db,worker_id='w',execution_id=execution_id,proof=body);db.commit();return reply
    supervisor=None
    if mode=='host':
        host.create(task)
        launch=json.loads((host.root/'launch.json').read_text());launch['command']=['/usr/bin/python3',str(tmp_path/'business.py')]
        (host.root/'launch.json').write_text(json.dumps(launch));host.start(task)
    else:
        docker('run','--detach','--name',name,'--label','mini-eval.execution='+execution,
               '--entrypoint','python',os.environ.get('RECOVERY_RUNTIME_IMAGE','mini-eval-api:recovery-foundations-20261125'),'-c',script)
        w.name=lambda e:'mini-eval-'+e;w.inspect=lambda e:Worker.inspect(w,e)
    try:
        runtime=RecoveryRuntime(w,execution);bound=runtime.inspect()['identity']
        w.state['recovery']={'generation':generation,'identity':bound}
        supervisor=RecoverySupervisor(tmp_path/'supervision',execution_id=execution,generation=generation,
            process_identity=bound,tolerance=60,started_at=0,requested_at=0)
        with TestClient(app) as client:
            def post(path,body):
                response=client.post('/api/v1/'+path,json=body)
                assert response.status_code==200,response.text
                return response.json()
            monkeypatch.setattr(recovery_control,'now',lambda:at)
            supervisor.exchange(SimpleNamespace(post=post),clock=lambda:0)
            # Both API and node see the same expired legacy lease. Allocation
            # remains protected, while the node keeps the original process.
            current=at+timedelta(seconds=35)
            monkeypatch.setattr(recovery_control,'now',lambda:current)
            with factory() as db:
                assert scheduling.utc(db.get(Run,'r').lease_expires_at)<current
                scheduling.reconcile(db,current);db.commit()
                assert db.get(Run,'r').status=='RUNNING'
            assert supervisor.tick(runtime.inspect,runtime.stop,at=35)=='track'
            supervisor.exchange(SimpleNamespace(post=post),clock=lambda:35)
            assert runtime.inspect()['identity']==bound
            assert supervisor.snapshot()['control']['clock']['confirmed_at']==35
            with factory() as db:
                run=db.get(Run,'r')
                assert run.execution_id==execution and run.status=='RUNNING'
                assert not run.cleanup_confirmed
                assert scheduling.utc(run.started_at)==at
            if cancel:
                assert runtime.inspect()['state']=='running'
                with factory() as db:db.get(Run,'r').status='CANCEL_REQUESTED';db.commit()
                supervisor.exchange(SimpleNamespace(post=post),clock=lambda:36)
                if stop_failure:
                    def unconfirmed(_):
                        raise RuntimeError('injected stop did not confirm termination')
                    with pytest.raises(RuntimeError, match='did not confirm'):
                        supervisor.tick(runtime.inspect,unconfirmed,at=36)
                    assert runtime.inspect()['state']=='running'
                    with factory() as db:
                        run=db.get(Run,'r')
                        assert run.status=='CANCEL_REQUESTED' and not run.cleanup_confirmed
                assert supervisor.tick(runtime.inspect,runtime.stop,at=36)=='stop'
                assert supervisor.tick(runtime.inspect,runtime.stop,at=37)=='retain'
                supervisor.exchange(SimpleNamespace(post=post),clock=lambda:37)
                actual=runtime.inspect()
                assert actual['state']=='exited'
                w.api=SimpleNamespace(post=post)
                Worker.finalize_recovery(w,{'status':'CANCELLED','exit_code':actual['exit_code']})
                assert w.state is None
                with factory() as db:
                    run=db.get(Run,'r')
                    assert run.status=='CANCELLED' and run.cleanup_confirmed
                    assert run.exit_code==actual['exit_code']
                return
            end=time.monotonic()+8
            while runtime.inspect()['state']=='running' and time.monotonic()<end:time.sleep(.05)
            assert supervisor.tick(runtime.inspect,runtime.stop,at=40)=='retain'
            supervisor.exchange(SimpleNamespace(post=post),clock=lambda:40)
            assert supervisor.snapshot()['exit_code']==0
            assert supervisor.snapshot()['control']['directive']=='retain'
    finally:
        if supervisor:supervisor.close()
        if mode=='host':host.remove(execution)
        else:docker('rm','--force',name,check=False)
        engine.dispose()


def test_created_container_is_not_a_successful_exit(tmp_path):
    w=host_worker(tmp_path,['python3','business.py']);w.host_mode=lambda:False
    w.inspect=lambda e:{'Id':'a'*64,'State':{'Status':'created','Running':False,'ExitCode':0}}
    runtime=RecoveryRuntime(w,w.state['task']['execution_id'])
    with pytest.raises(RuntimeError,match='has not started'):runtime.inspect()


@pytest.mark.parametrize('host',[True,False])
def test_launch_checks_original_marker_and_immutable_runtime(tmp_path,monkeypatch,host):
    import uuid
    w=host_worker(tmp_path,['python3','business.py']);w.host_mode=lambda:host
    marker='aaaaaaaa-aaaa-4aaa-aaaa-aaaaaaaaaaaa';w.state['task']['recovery_launch_id']=marker
    runtime=RecoveryRuntime(w,w.state['task']['execution_id'])
    data={'State':{'Status':'created','Running':False},'Id':'a'*64,'InvocationID':'b'*32,
          'LaunchID':marker,'Config':{'Labels':{'mini-eval.launch':marker}}}
    calls=[]
    monkeypatch.setattr(runtime,'_raw',lambda:data)
    def start(*args,**kwargs):
        calls.append(args);data['State']={'Status':'running','Running':True}
    monkeypatch.setattr(HostBackend,'start',start)
    monkeypatch.setattr('worker.client.docker',start)
    bound=runtime.start(marker)
    assert bound['launch_id']==marker and bound['identity']['runtime_id']==('b'*32 if host else 'a'*64)
    if not host:assert calls==[('start','a'*64)]
    with pytest.raises(RuntimeError,match='already started'):runtime.start(marker)
    with pytest.raises(ValueError):runtime.inspect_launch(str(uuid.uuid4()))
    with pytest.raises(ValueError):runtime.inspect_launch(marker.upper())
    data['LaunchID']='wrong';data['Config']['Labels']['mini-eval.launch']='wrong'
    with pytest.raises(RuntimeError,match='marker'):runtime.inspect_launch(marker)
    monkeypatch.setattr(runtime,'_raw',lambda:None)
    with pytest.raises(RuntimeError,match='marker'):runtime.start(marker)
    data['LaunchID']=marker;data['Config']['Labels']['mini-eval.launch']=marker
    data['State']['Recovered']=True
    monkeypatch.setattr(runtime,'_raw',lambda:data)
    with pytest.raises(RuntimeError,match='identity unavailable'):runtime.inspect_launch(marker)
