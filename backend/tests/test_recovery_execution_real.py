"""Real systemd/Docker through Worker.execute and isolated recovery HTTP/DB.

Synthetic short business programs; not model evaluation or deployment evidence.
"""
import copy
import json
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

from worker.client import Worker, docker
from worker.backpressure import APIError
from worker.host_backend import HostBackend
from worker.recovery_monitor import RecoveryMonitor
from worker.storage import atomic_json
from backend.tests.test_host_backend import worker as host_fixture


@pytest.mark.skipif(os.getenv('RECOVERY_RUNTIME_TEST')!='1',reason='real host/Docker execution main loop opt-in')
@pytest.mark.parametrize('mode',['host','docker'])
@pytest.mark.parametrize('scenario',['complete','command','stdout_burst','offline_complete','offline_cancel_after_exit','offline_timeout_after_exit','cancel','timeout','finite_disconnect','finite_reconnect','service','restart','pre_cancel','bulk_rejected'])
def test_real_execute_offline_reconnect_cleanup(tmp_path,mode,scenario):
    exercise(tmp_path, mode, scenario)


def exercise(tmp_path, mode, scenario, variant=None):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from backend.app import recovery_control,recovery_records,recovery_launch,recovery_claims,recovery_finish,recovery_unstarted,services
    from backend.app.core import Run,Job,EpisodeRecord,ExecutionRecoveryControl,ServiceInstance,now
    from backend.app.execution import Finish,RecoveryProof
    from backend.tests.test_execution_logs_postgres import seed
    from runner.sdk import episode_seed

    fixture=host_fixture(tmp_path,['python3','business.py'],timeout=1 if scenario=='timeout' else 0)
    w=object.__new__(Worker);w.__dict__.update(fixture.__dict__)
    w.host_mode=lambda:mode=='host';w.slot_id='s';w.start_lock=threading.Lock();w.stop_event=threading.Event()
    task=w.state['task'];task.update(benchmark='cs-client',job_id='j',run_id='r',episodes=1,lease_seconds=1)
    task['client_spec']['template'].update(task_type='evaluation',episode_reporting=False,integration_mode='direct-protocol')
    if scenario=='command':
        task['client_spec']['template'].update(task_type='command',integration_mode='direct')
    if scenario=='stdout_burst':
        task['log_protocol']=2
    if variant:
        from backend.tests.recovery_matrix_program import configure
        configure(task, mode, variant)
    task['client_spec']['timeout_seconds']=task['timeout_seconds']
    if scenario=='service':
        import socket
        with socket.socket() as port_socket:
            port_socket.bind(('127.0.0.1',0));port=port_socket.getsockname()[1]
        task.update(benchmark='inference-service',service_instance_id='svc')
        task['client_spec'].update(workload='service',service={
            'address':'127.0.0.1','port':port,'health_type':'tcp','startup_timeout':10})
    original=copy.deepcopy(task);execution=task['execution_id'];name='mini-eval-'+execution
    event={'episode_id':1,'seed':episode_seed(0,1),'status':'STARTED'}
    result={**event,'status':'COMPLETED','success':True,'metrics':{},'runtime':.4,'artifacts':[]}
    script='import time,signal,sys,os\nsignal.signal(signal.SIGTERM,lambda *a:sys.exit(0))\n'
    if scenario=='stdout_burst':
        script+='sys.stdout.write("x"*1000000+"\\n");sys.stdout.flush()\n'
    script+='print('+repr('MINI_EVAL_EVENT '+json.dumps(event))+',flush=True)\n'
    script+='time.sleep('+('2' if scenario in ('complete','stdout_burst','offline_complete','offline_cancel_after_exit','offline_timeout_after_exit','bulk_rejected','finite_reconnect') else '5' if scenario=='restart' else '30')+')\n'
    if scenario=='bulk_rejected':
        from worker.workspace import execution_dirs
        artifact_dir=(str(execution_dirs(w.workspace,execution)/'output') if mode=='host'
                      else '/tmp/mini-eval-output')
        script+='os.makedirs('+repr(artifact_dir)+',exist_ok=True)\n'
        script+='open(os.path.join('+repr(artifact_dir)+',"sample.json"),"w").write("{}")\n'
    script+='print('+repr('MINI_EVAL_EVENT '+json.dumps(result))+',flush=True)\n'
    script+='print('+repr(json.dumps({'final':{'success_rate':1.,'episode_count':1,'runtime':2.}}))+',flush=True)\n'
    if scenario=='command':
        script='import time\nprint("ordinary command started",flush=True)\ntime.sleep(2)\n'
    if scenario=='service':
        script='import socket,time\ns=socket.socket()\ns.bind(("127.0.0.1",'+str(port)+'))\ns.listen(32)\nprint("ready",flush=True)\ntime.sleep(30)\n'
    if variant:
        from backend.tests.recovery_matrix_program import program
        script = program(tmp_path, variant)
    (tmp_path/'business.py').write_text(script)
    engine=create_engine('sqlite:///'+str(tmp_path/'api.sqlite'),connect_args={'check_same_thread':False})
    factory=seed(engine)
    with factory() as db:
        run=db.get(Run,'r');run.execution_id=execution;run.status='STARTING';run.enqueued_at=now()
        job=db.get(Job,'j');job.benchmark=task['benchmark'];job.client_spec=task['client_spec'];job.episodes=task['episodes']
        if scenario=='service':
            job.service_instance_id='svc'
            db.add(ServiceInstance(id='svc',name='recovery-test',worker_id='w',slot_id='s',address='127.0.0.1',port=port,
                                   template_snapshot={},current_job_id='j'))
            db.flush()
        receipt=recovery_claims.bind(db,request_id=str(uuid.uuid4()),worker_id='w',slot_id='s',run_id='r',task=original,
                                     tolerance=1 if scenario=='finite_disconnect' else 4 if scenario=='finite_reconnect' else None)
        db.commit()
    w.state.update(created=False,offset=0,sequence=0,pending=None,recovery_assignment={
        'reconciliation_required':True,'claim':{'slot_id':'s','boot_id':Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
        'requested_at':time.monotonic(),'receipt':receipt}})
    atomic_json(w.path,w.state)
    def database():
        with factory() as db:yield db
    app=FastAPI()
    for module in (recovery_control,recovery_records,recovery_launch,recovery_unstarted):module.install(app,database,lambda:SimpleNamespace(id='w'))
    services.install(app,database,lambda:None,lambda:SimpleNamespace(id='w'))
    @app.post('/api/v1/worker/executions/{execution_id}/complete')
    def complete(execution_id:str,body:Finish):
        with factory() as db:
            reply=recovery_finish.finish(db,worker_id='w',execution_id=execution_id,body=body);db.commit();return reply
    @app.post('/api/v1/worker/executions/{execution_id}/cleanup')
    def cleanup(execution_id:str,body:RecoveryProof):
        with factory() as db:
            reply=recovery_finish.cleanup(db,worker_id='w',execution_id=execution_id,proof=body);db.commit();return reply
    @app.post('/api/v1/worker/executions/{execution_id}/recovery/results-finished')
    def results_finished(execution_id:str,body:RecoveryProof):
        with factory() as db:
            reply=recovery_finish.results_finished(db,worker_id='w',execution_id=execution_id,proof=body);db.commit();return reply
    host=HostBackend(w);calls=[];errors=[];offline=threading.Event();offline.set()
    def create(task):
        calls.append('create')
        if mode=='host':
            host.create(task)
            if not variant:
                path=host.root/'launch.json';value=json.loads(path.read_text())
                value['command']=['/usr/bin/python3',str(tmp_path/'business.py')];path.write_text(json.dumps(value))
        else:
            args=['create','--network','host','--name',name,'--label','mini-eval.execution='+execution,
                  '--label','mini-eval.launch='+task['recovery_launch_id']]
            if variant:
                from backend.tests.recovery_matrix_program import container_args
                args.extend(container_args(w, task, tmp_path))
                docker(*args)
                return
            if scenario=='stdout_burst':
                from worker.workspace import execution_dirs
                root=execution_dirs(w.workspace,execution)/'output'/'.mini-eval-protocol'
                root.mkdir()
                args.extend(['--workdir','/code','--mount',f'type=bind,source={root},target=/tmp/mini-eval-protocol',
                             '--mount',f'type=bind,source={Path(__file__).resolve().parents[2] / "runner"},target=/code/runner,readonly',
                             '--env','PYTHONPATH=/code'])
            args.extend(['--entrypoint','python',os.getenv('RECOVERY_RUNTIME_IMAGE','mini-eval-api:recovery-foundations-20261125')])
            args.extend(['-m','runner.log_capture','/tmp/mini-eval-protocol','python','-c',script]
                        if scenario=='stdout_burst' else ['-c',script])
            docker(*args)
    w.create=create
    def monitor(launch,control=False):
        m=RecoveryMonitor(w,launch,interval=.05)
        if control:m.start_control(w.api,interval=.1)
        return m
    w.monitor_recovery=monitor
    def run():
        try:w.execute(w.state['task'])
        except BaseException as exc:errors.append(exc)
    thread=None
    try:
        with TestClient(app) as client:
            def post(path,body=None):
                calls.append(path)
                if (scenario=='bulk_rejected' and
                        (path.endswith('/artifacts') or '/logs' in path)):
                    raise APIError(503,'injected bulk overload')
                if offline.is_set() and not path.endswith('/recovery/prepare'):raise TimeoutError('injected control/data outage')
                response=client.post('/api/v1/'+path,json=body)
                if response.status_code!=200:raise APIError(response.status_code,response.text)
                return response.json()
            w.api=SimpleNamespace(post=post)
            if scenario=='pre_cancel':
                offline.clear()
                with factory() as db:db.get(Run,'r').status='CANCEL_REQUESTED';db.commit()
                def lost_ack(path,body=None):
                    result=post(path,body)
                    if path.endswith('/recovery/not-started'):raise TimeoutError('lost cleanup ACK')
                    return result
                w.api=SimpleNamespace(post=lost_ack)
                with pytest.raises(TimeoutError):w.execute(w.state['task'])
                assert w.state is not None and w.inspect(execution) is None
                w.api=SimpleNamespace(post=post)
                w.execute(w.state['task'])
                assert w.state is None and calls.count('create')==1
                assert not Path(str(w.path)+'.supervision.json').exists()
                with factory() as db:
                    run=db.get(Run,'r')
                    assert run.status=='CANCELLED' and run.cleanup_confirmed
                    assert run.exit_code is None and run.started_at is None
                return
            thread=threading.Thread(target=run,daemon=True);thread.start()
            end=time.monotonic()+10
            while not Path(str(w.path)+'.supervision.json').exists() and time.monotonic()<end:time.sleep(.02)
            assert Path(str(w.path)+'.supervision.json').exists(),errors
            time.sleep(1.3)  # Exceeds this task's legacy one-second lease.
            assert thread.is_alive() and not errors
            if scenario in ('offline_complete','offline_cancel_after_exit','offline_timeout_after_exit'):
                end=time.monotonic()+10
                while time.monotonic()<end:
                    observed=w.inspect(execution)
                    if observed and not observed['State']['Running']:break
                    time.sleep(.05)
                assert observed and not observed['State']['Running'],errors
                time.sleep(1.3)
                assert thread.is_alive() and not errors
                assert calls.count('create')==1
                if scenario!='offline_complete':
                    with factory() as db:
                        db.get(Run,'r').status=('CANCEL_REQUESTED' if scenario=='offline_cancel_after_exit' else 'TIMED_OUT')
                        db.commit()
            if scenario=='restart':
                w.stop_event.set();thread.join(6)
                assert not thread.is_alive() and not errors
                assert w.inspect(execution)['State']['Running']
                w.state=json.loads(w.path.read_text())
                w.stop_event.clear()
                thread=threading.Thread(target=run,daemon=True);thread.start()
            if scenario=='cancel':
                with factory() as db:db.get(Run,'r').status='CANCEL_REQUESTED';db.commit()
            offline.clear()
            if scenario=='service':
                end=time.monotonic()+10
                while time.monotonic()<end:
                    with factory() as db:
                        if db.get(ServiceInstance,'svc').health_ready:break
                    time.sleep(.05)
                with factory() as db:
                    assert db.get(ServiceInstance,'svc').health_ready
                    db.get(Run,'r').status='CANCEL_REQUESTED';db.commit()
            thread.join(15)
            assert not thread.is_alive(),errors
            assert not errors,errors
            # Results may take another bounded pass after control cleanup.
            for _ in range(4):
                if w.state is None:break
                w.execute(w.state['task'])
            assert w.state is None
            with factory() as db:
                run=db.get(Run,'r')
                assert run.status=={'complete':'SUCCEEDED','command':'SUCCEEDED','stdout_burst':'SUCCEEDED','offline_complete':'SUCCEEDED',
                    'offline_cancel_after_exit':'CANCELLED','offline_timeout_after_exit':'TIMED_OUT','cancel':'CANCELLED',
                    'timeout':'TIMED_OUT','finite_disconnect':'TIMED_OUT','finite_reconnect':'SUCCEEDED','service':'CANCELLED','restart':'SUCCEEDED',
                    'bulk_rejected':'SUCCEEDED'}[scenario]
                assert run.cleanup_confirmed
                control=db.get(ExecutionRecoveryControl,execution).snapshot
                assert control['result_sync']=='complete'
                if scenario=='finite_disconnect':
                    assert control['stop_reason']=='disconnection_timeout'
                    assert db.get(EpisodeRecord,('r',1)).result is None
                if variant:
                    from backend.tests.recovery_matrix_program import verify
                    verify(db, tmp_path, variant)
                elif scenario in ('complete','stdout_burst','offline_complete','finite_reconnect','restart','bulk_rejected'):
                    assert db.get(EpisodeRecord,('r',1)).result==result
                if scenario=='command':
                    assert db.get(EpisodeRecord,('r',1)) is None
            if scenario=='bulk_rejected':
                assert any(path.endswith('/artifacts') for path in calls)
                assert any('/logs' in path for path in calls)
            assert calls.count('create')==1
    finally:
        offline.clear();w.stop_event.set()
        if thread:thread.join(10)
        if mode=='host':host.remove(execution)
        else:docker('rm','--force',name,check=False)
        engine.dispose()
