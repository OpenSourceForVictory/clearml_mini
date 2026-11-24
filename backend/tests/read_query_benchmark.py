"""Synthetic UI-read comparison. Caller supplies a new, empty, isolated database."""
import ast
import json
import subprocess
import time
from sqlalchemy import event, insert, select
from sqlalchemy.orm import sessionmaker
from backend.app.core import Base,User,Pool,Worker,Slot,EvaluationBatch,Job,Run,EpisodeRecord
from backend.app.administration import node_view,node_views
from backend.app.batches import batch_view,batch_summaries
from fastapi.encoders import jsonable_encoder


def run(engine):
    Base.metadata.create_all(engine)
    factory=sessionmaker(engine)
    with factory.begin() as s:
        s.add(User(id='bench',username='bench',password_hash='unused'));s.add(Pool(id='pool',name='pool'));s.flush()
        s.execute(insert(Worker),[dict(id=f'w{i}',node_name=f'w{i}',token_hash=f'{i:064x}') for i in range(500)])
        s.execute(insert(Slot),[dict(id=f's{i}',worker_id=f'w{i}',node_name=f'w{i}',gpu_index=0,gpu_uuid=f'GPU-{i}',pool_id='pool',queue_name=f'q{i}') for i in range(500)])
        s.execute(insert(EvaluationBatch),[dict(id=f'b{i}',owner_id='bench',title=f'batch{i}') for i in range(20)])
        s.execute(insert(Job),[dict(id=f'j{i}',batch_id=f'b{i//100}',owner_id='bench',pool_id='pool',title='done',benchmark='mock',model_ref='test',episodes=1,status='SUCCEEDED',client_spec={'template':{'task_type':'command','name':'test','version':'1','unused':'x'*2000},'task_name':'test','seed':0,'service_mode':'none'}) for i in range(2000)])
        s.execute(insert(Run),[dict(job_id=f'j{i}',attempt=1,status='SUCCEEDED',exit_code=0,cleanup_confirmed=True) for i in range(2000)])
    results={}
    for name in ('nodes_full','nodes_batched','batches_full','batches_summary'):
        samples=[]
        for repetition in range(4):
            sql=[]
            def trace(*args):sql.append(args[2])
            event.listen(engine,'before_cursor_execute',trace)
            try:
                start=time.perf_counter()
                with factory() as s:
                    if name.startswith('nodes'):
                        workers=list(s.scalars(select(Worker)))
                        value=node_views(s,workers) if name=='nodes_batched' else [node_view(s,w) for w in workers]
                    else:
                        batches=list(s.scalars(select(EvaluationBatch)))
                        value=batch_summaries(s,batches) if name=='batches_summary' else [batch_view(s,b) for b in batches]
                    data=json.dumps(jsonable_encoder(value)).encode()
                elapsed=time.perf_counter()-start
            finally:event.remove(engine,'before_cursor_execute',trace)
            if repetition:samples.append(dict(seconds=round(elapsed,5),sql=len(sql),bytes=len(data)))
        results[name]=samples
    # Compare the pre-change monitor against the current monitor on identical
    # data, using the compatible full view and the original function body.
    from backend.app import monitoring
    source=subprocess.check_output(['git','show','e85cbb0:backend/app/monitoring.py'],text=True)
    original=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='monitor')
    namespace=dict(monitoring.__dict__)
    exec(compile(ast.Module(body=[original],type_ignores=[]),'<baseline-monitor>','exec'),namespace)
    with factory.begin() as s:
        for j in s.scalars(select(Job).where(Job.batch_id=='b0')):
            j.episodes=10
            j.client_spec={**j.client_spec,'template':{'name':'test','version':'1','episode_reporting':True}}
        runs=list(s.scalars(select(Run).join(Job,Job.id==Run.job_id).where(Job.batch_id=='b0')))
        s.execute(insert(EpisodeRecord),[dict(run_id=r.id,episode_id=i+1,seed=i,started={'status':'STARTED'},result={'status':'COMPLETED','success':True,'exit_code':0,'metrics':{'payload':'x'*4000}}) for r in runs for i in range(10)])
    values={}
    for name,fn in [('monitor_before',namespace['monitor']),('monitor_after',monitoring.monitor)]:
        samples=[]
        for repetition in range(4):
            sql=[]
            def trace(*args):sql.append(args[2])
            event.listen(engine,'before_cursor_execute',trace)
            try:
                start=time.perf_counter()
                with factory() as s:value=fn(s,s.get(EvaluationBatch,'b0'))
                payload=json.dumps(jsonable_encoder(value)).encode()
                elapsed=time.perf_counter()-start
            finally:event.remove(engine,'before_cursor_execute',trace)
            if repetition:samples.append(dict(seconds=round(elapsed,5),sql=len(sql),bytes=len(payload)))
        values[name]=value
        results[name]=samples
    assert values['monitor_before']['summary']==values['monitor_after']['summary']
    assert values['monitor_before']['outcomes']==values['monitor_after']['outcomes']
    return results
