from sqlalchemy import select
from backend.tests.test_execution import env, node, submit, claim
from backend.app.core import Worker, ManagementAudit


def test_disabled_node_can_finish_but_not_claim(env):
    main,c,a=env;token,slots=node(env);job=submit(env);task=claim(env,token,slots[0])
    n=c.get('/api/v1/admin/nodes',headers=a['admin']).json()[0]
    assert n['roles']==['client']
    assert c.patch('/api/v1/admin/nodes/'+n['id'],headers=a['admin'],json={'enabled':False}).status_code==200
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slots[0],'execution_id':task['execution_id']}).status_code==200
    r=c.post('/api/v1/admin/nodes/'+n['id']+'/unmanage',headers=a['admin'],json={'expected_revision':1})
    assert r.status_code==409
    assert c.post('/api/v1/worker/executions/'+task['execution_id']+'/complete',headers=token,json={'status':'FAILED','error':'test'}).status_code==200
    r=c.post('/api/v1/admin/nodes/'+n['id']+'/unmanage',headers=a['admin'],json={'expected_revision':2})
    assert r.status_code==200 and r.json()['status']=='unmanaged'
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slots[0]}).status_code==401
    assert c.get('/api/v1/jobs/'+job,headers=a['alice']).status_code==200
    assert c.patch('/api/v1/admin/nodes/'+n['id'],headers=a['admin'],json={'enabled':True}).status_code==404


def test_reserved_disabled_and_role_conflicts(env):
    main,c,a=env;token,slots=node(env);submit(env)
    from backend.app.scheduling import tick
    tick(main.SessionLocal)
    n=c.get('/api/v1/admin/nodes',headers=a['admin']).json()[0]
    r=c.patch('/api/v1/admin/nodes/'+n['id'],headers=a['admin'],json={'roles':['server'],'expected_revision':0})
    assert r.status_code==200
    assert c.post('/api/v1/worker/slots/'+slots[0]+'/claim',headers=token).json()['task'] is None
    assert c.patch('/api/v1/admin/nodes/'+n['id'],headers=a['admin'],json={'enabled':True,'expected_revision':0}).status_code==409


def test_pool_partial_edit_and_archive(env):
    _,c,a=env
    r=c.post('/api/v1/admin/pools',headers=a['admin'],json={'name':'new','allow_shared':True,'max_tasks_per_gpu':3});pid=r.json()['id']
    assert c.patch('/api/v1/admin/pools/'+pid,headers=a['admin'],json={'name':'renamed'}).status_code==200
    p=next(p for p in c.get('/api/v1/admin/pools',headers=a['admin']).json() if p['id']==pid)
    assert p['allow_shared'] and p['max_tasks_per_gpu']==3
    assert c.delete('/api/v1/admin/pools/'+pid,headers=a['admin']).status_code==200
    assert c.patch('/api/v1/admin/pools/'+pid,headers=a['admin'],json={'enabled':True}).status_code==404
    node(env)
    assert c.delete('/api/v1/admin/pools/mock',headers=a['admin']).status_code==409


def test_accounts_and_redacted_audit(env):
    main,c,a=env
    assert c.get('/api/v1/admin/users',headers=a['alice']).status_code==403
    assert c.patch('/api/v1/admin/users/admin',headers=a['admin'],json={'role':'user'}).status_code==409
    assert c.patch('/api/v1/admin/users/alice',headers=a['admin'],json={'password':'new-long-password','active':False}).status_code==200
    assert c.get('/api/v1/auth/me',headers=a['alice']).status_code==401
    with main.SessionLocal() as s:
        logs=list(s.scalars(select(ManagementAudit)))
        assert logs and 'new-long-password' not in str([x.details for x in logs])
