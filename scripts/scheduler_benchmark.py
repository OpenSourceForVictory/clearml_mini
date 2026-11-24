"""Isolated scheduler measurements; never accepts a platform database URL."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import platform
import subprocess
import time
from uuid import uuid4
from unittest.mock import patch

from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker
from backend.app.core import Base, EvaluationBatch, Job, Pool, Run, User, Worker, Slot, now
from backend.app import scheduling, orchestration_scheduler


@contextmanager
def isolated_postgres():
    """Own a new loopback-only test container; remove only that exact identity."""
    name = 'mini-eval-scheduler-bench-' + uuid4().hex
    owner = uuid4().hex
    created = False
    engine = None
    try:
        subprocess.run(['docker','run','-d','--name',name,
                        '--label','mini-eval.scheduler-benchmark='+owner,
                        '-e','POSTGRES_HOST_AUTH_METHOD=trust',
                        '-p','127.0.0.1::5432','postgres:16.8'],check=True,capture_output=True)
        created = True
        info = json.loads(subprocess.check_output(['docker','inspect',name]))[0]
        port = int(info['NetworkSettings']['Ports']['5432/tcp'][0]['HostPort'])
        engine = create_engine(f'postgresql+psycopg://postgres@127.0.0.1:{port}/postgres',
                               connect_args={'connect_timeout':2})
        for attempt in range(60):
            try:
                with engine.connect() as c: c.execute(text('select 1'))
                break
            except Exception:
                if attempt == 59: raise
                time.sleep(.25)
        yield engine
    finally:
        if engine is not None: engine.dispose()
        if created:
            info = json.loads(subprocess.check_output(['docker','inspect',name]))[0]
            if info['Config']['Labels'].get('mini-eval.scheduler-benchmark') != owner:
                raise RuntimeError('test container identity changed; refusing removal')
            subprocess.run(['docker','rm','-f',info['Id']],check=True,capture_output=True)


def seed(engine, history, pending):
    if any(type(x) is not int or x < 0 or x > 10000 for x in (history,pending)):
        raise ValueError('history and pending must be integers in 0..10000')
    Base.metadata.create_all(engine)
    at = now()
    with Session(engine) as s:
        s.add(User(id='bench-user',username='bench-user',password_hash='disabled'))
        s.add(Pool(id='bench-pool',name='bench-pool'))
        s.add(Worker(id='bench-worker',node_name='bench-worker',token_hash='0'*64))
        s.flush()
        s.add(Slot(id='bench-slot',node_name='bench-worker',gpu_index=0,queue_name='bench-slot',
                   worker_id='bench-worker',pool_id='bench-pool',last_seen_at=at))
        s.flush()
        for i in range(history+pending):
            done = i < history
            identity = f'bench-{i:06d}'
            s.add(EvaluationBatch(id=identity,owner_id='bench-user',title=identity,created_at=at))
            s.flush()
            s.add(Job(id=identity,owner_id='bench-user',pool_id='bench-pool',batch_id=identity,
                      title=identity,benchmark='mock',model_ref='test',episodes=1,
                      status='SUCCEEDED' if done else 'PENDING',created_at=at))
            s.flush()
            s.add(Run(id=identity,job_id=identity,attempt=1,
                      status='SUCCEEDED' if done else 'PENDING',cleanup_confirmed=True))
        s.commit()
    return at


def measure(factory, at):
    """Rollback each sample so repeats use identical scheduling inputs."""
    engine = factory.kw['bind']
    metrics = {'sql_count':0,'sql_seconds':0.,'objects_loaded':0,'phases':{}}
    starts = []
    def before(*args): starts.append(time.perf_counter())
    def after(*args):
        metrics['sql_count'] += 1
        metrics['sql_seconds'] += time.perf_counter()-starts.pop()
    def loaded(*args): metrics['objects_loaded'] += 1
    class MeasurementSession(Session):
        def commit(self):
            # Measure flush; deliberately exclude commit durability and retain no changes.
            self.flush()
    event.listen(engine,'before_cursor_execute',before)
    event.listen(engine,'after_cursor_execute',after)
    event.listen(MeasurementSession,'loaded_as_persistent',loaded)
    measured_factory = sessionmaker(bind=engine,class_=MeasurementSession)
    def timed(name, fn):
        def wrapped(*args,**kwargs):
            start=time.perf_counter()
            try:return fn(*args,**kwargs)
            finally:metrics['phases'][name]=metrics['phases'].get(name,0.)+time.perf_counter()-start
        return wrapped
    from contextlib import ExitStack
    try:
        with ExitStack() as stack:
            for module,name in [(scheduling,'reconcile'),(orchestration_scheduler,'reconcile'),
                                (orchestration_scheduler,'fill_desired_instances')]:
                stack.enter_context(patch.object(module,name,timed(module.__name__+'.'+name,getattr(module,name))))
            start=time.perf_counter()
            metrics['admitted']=scheduling.tick(measured_factory,at)
            metrics['tick_seconds']=time.perf_counter()-start
    finally:
        event.remove(engine,'before_cursor_execute',before)
        event.remove(engine,'after_cursor_execute',after)
        event.remove(MeasurementSession,'loaded_as_persistent',loaded)
    return metrics


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--history',type=int,default=0)
    parser.add_argument('--pending',type=int,default=100)
    parser.add_argument('--repeat',type=int,default=5)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args(argv)
    if not 1 <= args.repeat <= 20 or not 0 <= args.history <= 10000 or not 0 <= args.pending <= 10000:
        parser.error('counts must be 0..10000 and repeat 1..20')
    result={'history':args.history,'pending':args.pending,'python':platform.python_version(),
            'cpu_count':os.cpu_count(),'samples':[],
            'boundary':'isolated PostgreSQL; mock ordinary jobs; rollback samples exclude commit durability; no HTTP/Worker load'}
    try:
        with isolated_postgres() as engine:
            with engine.connect() as c:result['postgres']=c.scalar(text('show server_version'))
            at=seed(engine,args.history,args.pending)
            factory=sessionmaker(bind=engine)
            measure(factory,at)
            for _ in range(args.repeat):result['samples'].append(measure(factory,at))
    except Exception as exc:
        result['failure']=type(exc).__name__
        raise
    finally:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(result,indent=2)+'\n')


if __name__=='__main__':main()
