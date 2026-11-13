import base64
import hashlib
import io
import zipfile
from sqlalchemy import select
from backend.tests.test_execution import env, node
from backend.app.core import Worker, Slot, now
from worker.version import PROTOCOL_VERSION, SOFTWARE_VERSION, CAPABILITIES


def request(env, count=2):
    main,c,a=env;old,ids=node(env,count=count)
    n=c.get('/api/v1/admin/nodes',headers=a['admin']).json()[0]
    with main.SessionLocal() as s:
        w=s.get(Worker,n['id']);w.storage_config={'workspace_root':'/mnt/keep'};w.runtime_config={'assets':{'data':'/mnt/assets'}};s.commit()
    path='/api/v1/admin/nodes/'+n['id']
    assert c.post(path+'/unmanage',headers=a['admin'],json={'expected_revision':0}).status_code==200
    r=c.post(path+'/reenroll',headers=a['admin'],json={'expected_revision':1})
    assert r.status_code==200,r.text
    return path,r.json(),old,ids


def connect(env,credential,indices):
    _,c,a=env
    token={'Authorization':'Bearer '+credential['token']}
    for i in indices:
        r=c.post('/api/v1/worker/register',headers=token,json={'pool_id':None,'gpu_index':i,'gpu_uuid':f'mock:node:{i}','gpu_model':'virtual','vram_gb':0,'protocol_version':PROTOCOL_VERSION,'software':{'version':SOFTWARE_VERSION,'build_digest':credential['release_id'],'capabilities':CAPABILITIES}})
        assert r.status_code==200,r.text
    return token


def test_reenrollment_identity_all_gpu_and_agent_gate(env):
    main,c,a=env;path,cred,old,ids=request(env)
    assert c.get('/api/v1/worker/self',headers=old).status_code==401
    assert c.patch(path,headers=a['admin'],json={'enabled':True}).status_code==409
    assert c.post(path+'/enrollment/complete',headers=a['admin'],json={'expected_revision':2}).status_code==409
    token=connect(env,cred,[0])
    package=c.get('/api/v1/admin/node-installation-bundle',headers=a['admin']).json()
    assert c.post('/api/v1/worker/upgrades/poll',headers=token,json={'agent_digest':package['agent_digest']}).status_code==200
    assert not c.get(path+'/enrollment',headers=a['admin']).json()['ready']
    connect(env,cred,[1])
    status=c.get(path+'/enrollment',headers=a['admin']).json()
    assert status['ready'] and {x['id'] for x in status['slots']}==set(ids)
    assert c.post(path+'/enrollment/complete',headers=a['admin'],json={'expected_revision':1}).status_code==409
    assert c.post(path+'/enrollment/complete',headers=a['admin'],json={'expected_revision':2}).status_code==200
    with main.SessionLocal() as s:
        w=s.get(Worker,cred['id']);assert w.enabled and w.storage_config=={'workspace_root':'/mnt/keep'}
        assert w.runtime_config=={'assets':{'data':'/mnt/assets'}}
        slots=list(s.scalars(select(Slot).where(Slot.worker_id==w.id)))
        assert {x.id for x in slots}==set(ids) and all(x.enabled and x.pool_id is None for x in slots)


def test_retry_rotates_pending_credentials_and_permissions(env):
    _,c,a=env;path,cred,_,_=request(env,1)
    assert c.post(path+'/reenroll',headers=a['alice'],json={'expected_revision':2}).status_code==403
    assert c.get('/api/v1/admin/node-installation-bundle',headers=a['alice']).status_code==403
    assert c.post(path+'/reenroll',headers=a['admin'],json={'expected_revision':1}).status_code==409
    new=c.post(path+'/reenroll',headers=a['admin'],json={'expected_revision':2})
    assert new.status_code==200 and new.json()['id']==cred['id']
    assert c.get('/api/v1/worker/self',headers={'Authorization':'Bearer '+cred['token']}).status_code==401
    assert not c.get(path+'/enrollment',headers=a['admin']).json()['ready']


def test_wrong_agent_digest_and_revocation_never_ready(env):
    _,c,a=env;path,cred,_,_=request(env,1);token=connect(env,cred,[0])
    c.post('/api/v1/worker/upgrades/poll',headers=token,json={'agent_digest':'a'*64})
    assert not c.get(path+'/enrollment',headers=a['admin']).json()['ready']
    assert c.post(path+'/unmanage',headers=a['admin'],json={'expected_revision':2}).status_code==200
    assert not c.get(path+'/enrollment',headers=a['admin']).json()['ready']


def test_bundle_contains_complete_platform_components(env):
    _,c,a=env;data=c.get('/api/v1/admin/node-installation-bundle',headers=a['admin']).json()
    payload=base64.b64decode(data['archive']);assert hashlib.sha256(payload).hexdigest()==data['sha256']
    from scripts.deploy_node import validate_bundle
    validate_bundle(payload,data['sha256'])
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        assert {'worker/client.py','worker/updater.py','runner/sdk.py','scripts/install_node.py','scripts/deploy_node.py','scripts/install_worker_updater.py'}<=set(z.namelist())
        assert hashlib.sha256(z.read('worker-release.zip')).hexdigest()==data['release_id']
        assert hashlib.sha256(z.read('worker/updater.py')).hexdigest()==data['agent_digest']
