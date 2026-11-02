from datetime import timedelta
import pytest
from sqlalchemy import select
from backend.tests.test_execution import env, node
from backend.tests.test_cs_protocol import template, submit_body
from backend.app.core import Run, Slot, ClientTemplate, now
from backend.app.scheduling import tick, transition
from backend.app.endpoints import resource_key

A='http://192.168.137.223:19090/infer'
B='http://192.168.137.223:19091/infer'


def setup(env, monkeypatch, count=3):
    main,c,auth=env
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL','20');monkeypatch.setenv('MAX_ACTIVE_PER_USER','20')
    t=template(env);token,slots=node(env,count=count)
    with main.SessionLocal() as s:
        s.get(ClientTemplate,t).endpoint_exclusive=True
        for sid in slots:
            slot=s.get(Slot,sid);slot.protocol_version=3;slot.client_images=['approved:cs']
        s.commit()
    return main,c,auth,t,token,slots


def body(t, values=None):
    result=submit_body(t)
    if values is not None:
        result['client'].pop('endpoint')
        result['client']['endpoints']=[{'url':url,'mode':mode} for url,mode in values]
    return result


def submit(c,auth,t,values,owner='alice'):
    response=c.post('/api/v1/jobs',headers=auth[owner],json=body(t,values))
    assert response.status_code==201,response.text
    return response.json()['id']


def test_lists_atomic_validation_and_legacy_defaults(env,monkeypatch):
    main,c,auth,t,token,slots=setup(env,monkeypatch)
    for values in [[],[(A,'invalid')],[(A,'shared'),(A.replace('/infer','/other'),'exclusive')],[(A,'shared')]*101,[('ws://host:8000','shared')],[('http://localhost:80','shared')]]:
        assert c.post('/api/v1/jobs',headers=auth['alice'],json=body(t,values)).status_code==400
    both=body(t,[(A,'shared')]);both['client']['endpoint']=A
    assert c.post('/api/v1/jobs',headers=auth['alice'],json=both).status_code==400
    assert resource_key('http://EXAMPLE.com./x')==resource_key('ws://example.com:80/y')
    assert resource_key('http://[2001:db8::1]:80')==resource_key('http://[2001:0db8:0:0:0:0:0:1]')
    old=submit(c,auth,t,None)
    assert tick(main.SessionLocal)==1
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.job_id==old));assert run.assigned_endpoint==A and run.assigned_endpoint_mode=='exclusive'
    submit(c,auth,t,[(A,'shared')],'bob')
    assert tick(main.SessionLocal)==0


def test_two_exclusive_addresses_wait_and_cleanup_release(env,monkeypatch):
    main,c,auth,t,token,slots=setup(env,monkeypatch)
    jobs=[submit(c,auth,t,[(A,'exclusive'),(B,'exclusive')]) for _ in range(3)]
    assert tick(main.SessionLocal)==2
    with main.SessionLocal() as s:
        runs=list(s.scalars(select(Run).where(Run.status=='RESERVED')))
        assert {r.assigned_endpoint for r in runs}=={A,B}
        sid,ex,jid=runs[0].slot_id,runs[0].execution_id,runs[0].job_id
    task=c.post(f'/api/v1/worker/slots/{sid}/claim',headers=token).json()['task']
    assert 'endpoints' not in task['client_spec']
    assert task==c.post(f'/api/v1/worker/slots/{sid}/claim',headers=token).json()['task']
    assert c.post(f'/api/v1/jobs/{jid}/cancel',headers=auth['alice']).status_code==202
    assert tick(main.SessionLocal)==0
    with main.SessionLocal() as s:
        r=s.scalar(select(Run).where(Run.execution_id==ex));transition(s,r,'CANCELLED');s.commit()
    assert tick(main.SessionLocal)==0
    assert c.post(f'/api/v1/worker/executions/{ex}/cleanup',headers=token).status_code==200
    assert tick(main.SessionLocal)==1


def test_shared_override_template_and_bypass_busy_first(env,monkeypatch):
    main,c,auth,t,token,slots=setup(env,monkeypatch)
    submit(c,auth,t,[(A,'exclusive')]);assert tick(main.SessionLocal)==1
    jobs=[submit(c,auth,t,[(A,'shared'),(B,'shared')],owner) for owner in ['alice','bob']]
    assert tick(main.SessionLocal)==2
    with main.SessionLocal() as s:
        for jid in jobs:
            r=s.scalar(select(Run).where(Run.job_id==jid));assert (r.assigned_endpoint,r.assigned_endpoint_mode)==(B,'shared')
    submit(c,auth,t,[(B,'exclusive')]);assert tick(main.SessionLocal)==0


def test_preflight_is_read_only_reports_candidates_and_actual(env,monkeypatch):
    main,c,auth,t,token,slots=setup(env,monkeypatch)
    submit(c,auth,t,[(A,'exclusive')]);assert tick(main.SessionLocal)==1
    value={'title':'batch','pool_id':'mock','max_concurrent':2,'cases':[{'case_id':'case','task_name':'demo','episodes':1,'seed':0}],
           'client':body(t,[(A,'exclusive'),(B,'exclusive')])['client']}
    p=c.post('/api/v1/batches/preflight',headers=auth['alice'],json=value)
    assert p.status_code==200,p.text
    assert [e['available'] for e in p.json()['endpoints']]==[False,True]
    assert 'endpoint_busy' not in [r['code'] for r in p.json()['reasons']]
    b=c.post('/api/v1/batches',headers=auth['alice'],json=value);assert b.status_code==201,b.text
    bid=b.json()['id'];jid=b.json()['cases'][0]['job_id']
    report=c.get(f'/api/v1/batches/{bid}/report',headers=auth['alice']).json()['cases'][0]
    assert report['config']['endpoint'] is None and len(report['config']['endpoint_candidates'])==2
    assert tick(main.SessionLocal)==1
    case=c.get(f'/api/v1/batches/{bid}/monitor',headers=auth['alice']).json()['cases'][0]
    assert case['config']['endpoint']==B and case['config']['endpoint_mode']=='exclusive'
    assert c.get(f'/api/v1/batches/{bid}/report',headers=auth['bob']).status_code==404
    assert B in c.get(f'/api/v1/batches/{bid}/report?format=csv',headers=auth['alice']).text
    assert c.get(f'/api/v1/jobs/{jid}',headers=auth['alice']).json()['attempts'][0]['endpoint']==B


def test_reservation_reset_and_retry_keep_history(env,monkeypatch):
    main,c,auth,t,token,slots=setup(env,monkeypatch)
    jid=submit(c,auth,t,[(A,'exclusive'),(B,'exclusive')]);tick(main.SessionLocal)
    with main.SessionLocal() as s:
        r=s.scalar(select(Run).where(Run.job_id==jid));sid=r.slot_id
        r.reservation_expires_at=now()-timedelta(seconds=1)
        # Make worker offline so reconcile cannot immediately readmit.
        for slot in s.scalars(select(Slot)):slot.last_seen_at=now()-timedelta(minutes=2)
        s.commit()
    tick(main.SessionLocal)
    with main.SessionLocal() as s:
        r=s.scalar(select(Run).where(Run.job_id==jid));assert r.status=='PENDING' and r.assigned_endpoint is None
        for slot in s.scalars(select(Slot)):slot.last_seen_at=now()
        s.commit()
    tick(main.SessionLocal)
    with main.SessionLocal() as s:
        r=s.scalar(select(Run).where(Run.job_id==jid));sid=r.slot_id;ex=r.execution_id
    c.post(f'/api/v1/worker/slots/{sid}/claim',headers=token)
    c.post(f'/api/v1/worker/executions/{ex}/complete',headers=token,json={'status':'FAILED','error':'injected'})
    submit(c,auth,t,[(A,'exclusive')],'bob');tick(main.SessionLocal)
    assert c.post(f'/api/v1/jobs/{jid}/retry',headers=auth['alice']).status_code==202
    tick(main.SessionLocal)
    attempts=c.get(f'/api/v1/jobs/{jid}',headers=auth['alice']).json()['attempts']
    assert [r['endpoint'] for r in attempts]==[A,B]

@pytest.mark.parametrize('action',['cancel','pause','membership','storage','runtime'])
def test_unclaimed_binding_withdrawal_paths(env,monkeypatch,action):
    main,c,auth,t,token,slots=setup(env,monkeypatch,count=1)
    value={'title':'withdraw','pool_id':'mock','max_concurrent':1,'cases':[{'case_id':'x','task_name':'demo','episodes':1,'seed':0}],
           'client':body(t,[(A,'exclusive')])['client']}
    b=c.post('/api/v1/batches',headers=auth['alice'],json=value).json();jid=b['cases'][0]['job_id'];tick(main.SessionLocal)
    with main.SessionLocal() as s:wid=s.get(Slot,slots[0]).worker_id
    if action=='cancel':response=c.post(f'/api/v1/jobs/{jid}/cancel',headers=auth['alice'])
    elif action=='pause':response=c.post(f'/api/v1/batches/{b["id"]}/control',headers=auth['alice'],json={'action':'pause','expected_revision':0})
    elif action=='membership':response=c.put('/api/v1/admin/resource-memberships',headers=auth['admin'],json={'pool_id':None,'members':[{'slot_id':slots[0],'expected_revision':0}]})
    else:response=c.put(f'/api/v1/admin/nodes/{wid}/{action}',headers=auth['admin'],json={'expected_revision':0,'config':None})
    assert response.status_code in (200,202),response.text
    with main.SessionLocal() as s:
        r=s.scalar(select(Run).where(Run.job_id==jid));assert r.assigned_endpoint is None and r.assigned_endpoint_mode is None
    assert c.get(f'/api/v1/jobs/{jid}',headers=auth['alice']).json()['attempts'][0]['endpoint'] is None


def test_no_slot_does_not_hold_address_and_shared_balances(env,monkeypatch):
    main,c,auth,t,token,slots=setup(env,monkeypatch,count=3)
    with main.SessionLocal() as s:
        for slot in s.scalars(select(Slot)):slot.last_seen_at=now()-timedelta(minutes=2)
        s.commit()
    for _ in range(3):submit(c,auth,t,[(A,'shared'),(B,'shared')])
    assert tick(main.SessionLocal)==0
    with main.SessionLocal() as s:
        assert all(r.assigned_endpoint is None for r in s.scalars(select(Run)))
        for slot in s.scalars(select(Slot)):slot.last_seen_at=now()
        s.commit()
    assert tick(main.SessionLocal)==3
    with main.SessionLocal() as s:
        chosen=[r.assigned_endpoint for r in s.scalars(select(Run))]
        assert chosen.count(A)==2 and chosen.count(B)==1


def test_batch_validation_and_cosmos_explicit_shared(env,monkeypatch):
    main,c,auth,t,token,slots=setup(env,monkeypatch)
    from pathlib import Path
    import json
    manifests=list(Path('examples/cosmos_robotwin').glob('*template*.json'))
    assert manifests
    definition=json.loads(manifests[0].read_text())
    with main.SessionLocal() as s:
        template_row=s.get(ClientTemplate,t)
        template_row.protocols=definition['protocols'];template_row.endpoint_exclusive=definition['endpoint_exclusive'];s.commit()
    url='ws://inference:8000'
    value={'title':'cosmos','pool_id':'mock','max_concurrent':2,'cases':[{'case_id':'x','task_name':'adjust_bottle','episodes':1,'seed':0}],
           'client':body(t,[(url,'shared')])['client']}
    for path in ['/api/v1/batches','/api/v1/batches/preflight']:
        invalid={**value,'client':{**value['client'],'endpoints':[]}}
        assert c.post(path,headers=auth['alice'],json=invalid).status_code==400
    for _ in range(2):assert c.post('/api/v1/batches',headers=auth['alice'],json=value).status_code==201
    assert tick(main.SessionLocal)==2
    with main.SessionLocal() as s:assert all(r.assigned_endpoint_mode=='shared' for r in s.scalars(select(Run)))
