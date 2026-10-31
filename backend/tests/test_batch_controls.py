from sqlalchemy import select
from backend.tests.test_execution import env, node
from backend.tests.test_cs_protocol import template, batch_body
from backend.app.core import EvaluationBatch, Job, Run, Slot, Event
from backend.app.scheduling import tick, transition


def setup(env):
    main,c,auth=env
    token,slots=node(env,count=2)
    with main.SessionLocal() as s:
        for sid in slots:
            x=s.get(Slot,sid);x.protocol_version=3;x.client_images=['approved:cs']
        s.commit()
    body=batch_body(template(env));body['max_concurrent']=2
    b=c.post('/api/v1/batches',headers=auth['alice'],json=body).json()
    return b,token,slots


def control(env,b,action,rev,owner='alice'):
    _,c,auth=env
    return c.post('/api/v1/batches/'+b['id']+'/control',headers=auth[owner],json={'action':action,'expected_revision':rev})


def monitor(env,b):
    _,c,auth=env
    return c.get('/api/v1/batches/'+b['id']+'/monitor',headers=auth['alice']).json()


def test_pause_withdraw_resume_and_stale_requests(env):
    main,c,auth=env;b,token,slots=setup(env)
    assert b['scheduling_paused'] is False and b['control_revision']==0
    assert tick(main.SessionLocal)==2
    assert control(env,b,'pause',0,'bob').status_code==404
    r=control(env,b,'pause',0);assert r.status_code==200 and r.json()['counts']['withdrawn']==2
    with main.SessionLocal() as s:
        assert s.get(EvaluationBatch,b['id']).scheduling_paused
        for case in b['cases']:
            run=s.get(Run,case['run_id']);assert run.status=='PENDING' and run.attempt==1
            assert run.slot_id is None and run.execution_id is None and run.reserved_cpu==run.reserved_ram_gb==run.reserved_vram_mb==0
    for sid in slots:assert c.post(f'/api/v1/worker/slots/{sid}/claim',headers=token).json()['task'] is None
    assert tick(main.SessionLocal)==0
    assert all('batch_paused' in x['reason_codes'] for x in monitor(env,b)['cases'])
    assert control(env,b,'pause',1).json()['control_revision']==2
    assert control(env,b,'resume',2,'admin').status_code==200
    assert control(env,b,'pause',0).status_code==409
    assert not monitor(env,b)['scheduling_paused']
    assert tick(main.SessionLocal)==2


def test_claimed_execution_survives_pause_and_reconnect(env):
    main,c,auth=env;b,token,slots=setup(env);tick(main.SessionLocal)
    task=c.post(f'/api/v1/worker/slots/{slots[0]}/claim',headers=token).json()['task']
    assert task
    r=control(env,b,'pause',0).json();assert r['counts']['withdrawn']==1
    again=c.post(f'/api/v1/worker/slots/{slots[0]}/claim',headers=token).json()['task']
    assert again['execution_id']==task['execution_id']
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slots[0],'execution_id':task['execution_id']}).json()['cancel'] is False
    assert tick(main.SessionLocal)==0
    # Defence at claim also applies if an old RESERVED row exists while paused.
    with main.SessionLocal() as s:
        j=s.get(Job,next(x['job_id'] for x in b['cases'] if x['job_id']!=task['job_id']))
        run=j.runs[-1];run.slot_id=slots[1];transition(s,run,'RESERVED');s.commit()
    assert c.post(f'/api/v1/worker/slots/{slots[1]}/claim',headers=token).json()['task'] is None


def test_cancel_cleanup_retry_and_resume(env):
    main,c,auth=env;b,token,slots=setup(env);tick(main.SessionLocal)
    task=c.post(f'/api/v1/worker/slots/{slots[0]}/claim',headers=token).json()['task']
    r=control(env,b,'cancel',0).json();assert r['counts']['cancelled']==1 and r['counts']['cancel_requested']==1
    m=monitor(env,b);assert m['summary']['cancel_requested_tasks']==m['summary']['cleanup_pending_tasks']==1
    assert c.post('/api/v1/jobs/'+task['job_id']+'/retry',headers=auth['alice']).status_code==409
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slots[0],'execution_id':task['execution_id']}).json()['cancel'] is True
    with main.SessionLocal() as s:
        before=len(s.scalars(select(Event)).all())
    r=control(env,b,'cancel',1).json();assert r['counts']['already_cancelling']==1 and r['counts']['terminal_unchanged']==1
    with main.SessionLocal() as s:assert len(s.scalars(select(Event)).all())==before
    assert control(env,b,'resume',2).status_code==200
    assert tick(main.SessionLocal)==0
    # Simulate the existing Worker cleanup confirmation, preserving the attempt.
    with main.SessionLocal() as s:
        run=s.get(Run,task['run_id']);transition(s,run,'CANCELLED');run.cleanup_confirmed=True;s.commit()
    assert control(env,b,'pause',3).status_code==200
    assert c.post('/api/v1/jobs/'+task['job_id']+'/retry',headers=auth['alice']).status_code==202
    assert tick(main.SessionLocal)==0
    assert control(env,b,'cancel',0).status_code==409
    assert control(env,b,'resume',4).status_code==200
    assert tick(main.SessionLocal)==1


def test_terminal_history_and_bulk_retry_remain_paused(env):
    main,c,auth=env;b,_,_=setup(env)
    with main.SessionLocal() as s:
        for n,case in enumerate(b['cases']):
            run=s.get(Run,case['run_id']);transition(s,run,'FAILED' if n else 'SUCCEEDED','historical result')
        s.commit()
    r=control(env,b,'cancel',0).json();assert r['counts']['terminal_unchanged']==2
    failed=b['cases'][1]
    r=c.post('/api/v1/batches/'+b['id']+'/retry-failed',headers=auth['alice'],json={'items':[{'job_id':failed['job_id'],'expected_run_id':failed['run_id']}]})
    assert r.json()['retried']==1 and tick(main.SessionLocal)==0
    with main.SessionLocal() as s:assert s.get(Run,failed['run_id']).error=='historical result'
    assert c.post('/api/v1/jobs/'+b['cases'][0]['job_id']+'/cancel',headers=auth['alice']).status_code==409


def test_paused_batch_does_not_block_other_batch_after_api_reload(env):
    import importlib
    main,c,auth=env;b,_,_=setup(env)
    with main.SessionLocal() as s:tid=s.get(Job,b['cases'][0]['job_id']).client_spec['template']['id']
    other=c.post('/api/v1/batches',headers=auth['alice'],json={**batch_body(tid),'max_concurrent':2}).json()
    assert control(env,b,'pause',0).status_code==200
    importlib.reload(main)
    assert tick(main.SessionLocal)==2
    with main.SessionLocal() as s:
        assert s.get(EvaluationBatch,b['id']).scheduling_paused
        assert all(s.get(Job,x['job_id']).status=='PENDING' for x in b['cases'])
        assert all(s.get(Job,x['job_id']).status=='RESERVED' for x in other['cases'])
