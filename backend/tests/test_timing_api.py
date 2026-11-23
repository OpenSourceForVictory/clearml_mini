from datetime import timedelta
import csv,io
from backend.tests.test_execution import env
from backend.tests.test_monitoring import batch
from backend.tests.test_sdk_reporting_contract import reporting,start,end
from backend.app.core import Run,Job,EpisodeRecord,Event,EvaluationBatch,now
from backend.app import execution_timing as t


def test_receipt_idempotency_private_fields_and_interruption(env):
    c,token,base,view,task=reporting(env)
    with env[0].SessionLocal() as s:
        job=s.get(Job,task['job_id']);job.client_spec={**job.client_spec,'template':{**job.client_spec['template'],'integration_mode':'sdk-command'}};s.commit()
    assert c.post(base+'/episodes',headers=token,json=start()).status_code==200
    with env[0].SessionLocal() as s:receipt=s.get(EpisodeRecord,(task['run_id'],1)).started[t.RECEIVED]
    assert c.post(base+'/episodes',headers=token,json=start()).status_code==200
    with env[0].SessionLocal() as s:assert s.get(EpisodeRecord,(task['run_id'],1)).started[t.RECEIVED]==receipt
    result=c.get(view,headers=env[2]['alice']).json()
    assert result['items'][0]['started']==start() and t.RECEIVED not in str(result)
    assert result['items'][0]['timing']['state']=='running'
    assert c.post(base+'/episodes',headers=token,json={**start(),t.RECEIVED:receipt}).status_code==400
    assert c.get(view,headers=env[2]['bob']).status_code==404
    assert c.post(base+'/episodes',headers=env[2]['alice'],json=start()).status_code==401
    with env[0].SessionLocal() as s:
        run=s.get(Run,task['run_id']);run.status='TIMED_OUT';run.finished_at=now();s.commit()
    result=c.get(view,headers=env[2]['alice']).json()
    assert result['items'][0]['timing']['state']=='interrupted'
    assert result['timing']['execution']['state']=='finished'


def test_timed_events_conflicts_legacy_and_planned(env):
    c,token,base,view,task=reporting(env)
    stamp=now().isoformat();timing={'schema':1,'started_at':stamp}
    first={**start(),'timing':timing}
    assert c.post(base+'/episodes',headers=token,json=first).status_code==200
    final={**end(),'timing':{**timing,'finished_at':stamp,'duration_seconds':.012,'clock_adjusted':False}}
    mismatched={**final,'timing':{**final['timing'],'started_at':'2026-01-01T00:00:00+00:00'}}
    assert c.post(base+'/episodes',headers=token,json=mismatched).status_code==409
    for _ in range(2):assert c.post(base+'/episodes',headers=token,json=final).status_code==200
    assert c.post(base+'/episodes',headers=token,json={**final,'timing':{**final['timing'],'duration_seconds':2}}).status_code==409
    assert c.post(base+'/episodes',headers=token,json=start()).status_code==409
    result=c.get(view+'?include_planned=true',headers=env[2]['alice']).json()
    assert len(result['items'])==2 and result['items'][0]['timing'] is None
    # Rows written before receipt metadata are still replayable.
    with env[0].SessionLocal() as s:s.add(EpisodeRecord(run_id=task['run_id'],episode_id=2,seed=8,started=start(2)));s.commit()
    assert c.post(base+'/episodes',headers=token,json=start(2)).status_code==200


def test_task_monitor_export_and_retry_use_same_attempt_clock(env):
    main,c,auth=env;b,_=batch(env)
    stamp=now()-timedelta(minutes=3)
    with main.SessionLocal() as s:
        s.get(EvaluationBatch,b['id']).created_at=stamp-timedelta(seconds=10)
        for item in b['cases']:
            r=s.get(Run,item['run_id']);r.status='FAILED';r.enqueued_at=stamp;r.finished_at=stamp+timedelta(seconds=30);r.cleanup_confirmed=True
            j=s.get(Job,r.job_id);j.status='FAILED'
        s.commit()
    m=c.get('/api/v1/batches/'+b['id']+'/monitor',headers=auth['alice']).json()
    assert m['timing']['elapsed']['seconds']==40
    assert all(x['timing']['execution']['seconds']==30 for x in m['cases'])
    assert all(x['timing']['as_of']==m['timing']['as_of'] for x in m['cases'])
    report=c.get('/api/v1/batches/'+b['id']+'/report',headers=auth['alice']).json()
    assert report['timing']['elapsed']['seconds']==40
    assert all(x['timing']['execution']['seconds']==30 for x in report['cases'])
    raw=c.get('/api/v1/batches/'+b['id']+'/report?format=csv',headers=auth['alice'])
    assert list(csv.DictReader(io.StringIO(raw.content.decode('utf-8-sig'))))[0]['execution_seconds']=='30.0'
    item=b['cases'][0]
    assert c.post('/api/v1/jobs/'+item['job_id']+'/retry',headers=auth['alice']).status_code==202
    detail=c.get('/api/v1/jobs/'+item['job_id'],headers=auth['alice']).json()
    assert detail['attempts'][0]['timing']['execution']['seconds']==30
    assert detail['attempts'][1]['timing']['execution']['state']=='not_started'
    assert detail['attempts'][1]['timing']['waiting']['state']=='running'


import pytest
@pytest.mark.parametrize('capable',[True,False])
def test_claim_only_negotiates_injected_runner(env,capable):
    from backend.tests.test_image_runtime import runtime_setup
    from backend.tests.test_cs_protocol import submit_body
    from backend.tests.test_execution import claim
    from backend.app.core import Slot
    main,c,auth=env
    template,token,slot,_,report=runtime_setup(env)
    with main.SessionLocal() as s:
        s.get(Slot,slot).protocol_version=18
        s.get(Slot,slot).software={'capabilities':['episode_timing_v1'] if capable else []};s.commit()
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slot,'runtime':report}).status_code==200
    assert c.post('/api/v1/jobs',headers=auth['alice'],json=submit_body(template)).status_code==201
    assert bool(claim(env,token,slot).get('episode_timing_v1'))==capable


def test_empty_batch_and_missing_terminal_timestamps(env):
    main,c,auth=env
    with main.SessionLocal() as s:
        empty=EvaluationBatch(owner_id='alice',title='empty');s.add(empty);s.commit();identity=empty.id
    result=c.get('/api/v1/batches/'+identity+'/monitor',headers=auth['alice'])
    assert result.status_code==200,result.text
    assert result.json()['timing']['elapsed']['state']=='unknown'
    b,_=batch(env)
    with main.SessionLocal() as s:
        for item in b['cases']:
            r=s.get(Run,item['run_id']);r.status='FAILED';r.enqueued_at=now();r.finished_at=None
            s.get(Job,r.job_id).status='FAILED'
        s.commit()
    value=c.get('/api/v1/batches/'+b['id']+'/monitor',headers=auth['alice']).json()
    assert value['timing']['elapsed']['state']=='unknown'
    assert all(x['timing']['execution']['state']=='unknown' for x in value['cases'])


def test_job_lists_share_clock_and_batch_event_query(env):
    from sqlalchemy import event
    main,c,auth=env;b,_=batch(env)
    statements=[];engine=main.SessionLocal.kw['bind']
    def trace(conn,cursor,statement,parameters,context,many):statements.append(statement)
    for path in ('jobs','admin/jobs'):
        statements.clear();event.listen(engine,'before_cursor_execute',trace)
        try:response=c.get('/api/v1/'+path,headers=auth['admin'] if path.startswith('admin') else auth['alice'])
        finally:event.remove(engine,'before_cursor_execute',trace)
        assert response.status_code==200,response.text
        assert len({r['timing']['as_of'] for j in response.json() for r in j['attempts']})==1
        assert sum('FROM job_events' in sql for sql in statements)==1
