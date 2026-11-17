import json
import time
import pytest
from backend.tests.test_execution import env,node,submit,claim
from backend.app.core import Worker,Slot
from backend.app import ssh_onboarding
from worker.version import SOFTWARE_VERSION,PROTOCOL_VERSION,CAPABILITIES
from onboarding.contracts import validate,Rejected
from onboarding.executor import Operations


def form(**kw):
    return dict(host='127.0.0.1',port=22,username='test',node_name='ssh-test',api_url='http://127.0.0.1:18093',mode='host',gpus='all',password='SECRET-PASSWORD',request_id='request-one',**kw)


def wait(fn):
    for _ in range(200):
        if value:=fn():return value
        time.sleep(.01)
    raise AssertionError('operation did not settle')


def test_api_permissions_secret_safe_validation_and_outage_isolation(env,monkeypatch):
    _,c,a=env
    r=c.post('/api/v1/admin/ssh-onboarding/check',headers=a['alice'],json=form());assert r.status_code==403
    r=c.post('/api/v1/admin/ssh-onboarding/check',headers=a['admin'],json={**form(),'port':'SECRET-PASSWORD'});assert r.status_code==400 and 'SECRET' not in r.text
    token,slots=node(env);submit(env);task=claim(env,token,slots[0])
    monkeypatch.delenv('ONBOARDING_SOCKET',raising=False)
    assert c.get('/api/v1/admin/ssh-onboarding',headers=a['admin']).status_code==503
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slots[0],'execution_id':task['execution_id']}).status_code==200


def test_new_node_only_planned_gpus_register_and_all_components_gate(env):
    main,c,a=env
    package=c.get('/api/v1/admin/node-installation-bundle',headers=a['admin']).json()
    body={'node_name':'fresh','roles':['client'],'release_id':package['release_id'],'devices':[{'index':0,'uuid':'mock:fresh:0'},{'index':1,'uuid':'mock:fresh:1'}],'operation_id':'a'*32}
    response=c.post('/api/v1/admin/ssh-enrollment/begin',headers=a['admin'],json=body)
    assert response.status_code==200,response.text
    n=response.json();token={'Authorization':'Bearer '+n['token']};prefix='/api/v1/admin/nodes/'+n['id']
    def register(index):return c.post('/api/v1/worker/register',headers=token,json={'gpu_index':index,'gpu_uuid':f'mock:fresh:{index}','gpu_model':'virtual','vram_gb':0,'protocol_version':PROTOCOL_VERSION,'software':{'version':SOFTWARE_VERSION,'build_digest':package['release_id'],'capabilities':CAPABILITIES}})
    assert register(5).status_code==409
    assert register(0).status_code==200
    assert c.post(prefix+'/enrollment/complete',headers=a['admin'],json={'expected_revision':1}).status_code==409
    assert register(1).status_code==200
    c.post('/api/v1/worker/upgrades/poll',headers=token,json={'agent_digest':package['agent_digest']})
    assert not c.get(prefix+'/enrollment',headers=a['admin']).json()['ready']
    from backend.tests.test_enrollment import connect_agent
    connect_agent(c,token,package['release_id'])
    assert c.get(prefix+'/enrollment',headers=a['admin']).json()['ready']
    assert c.post(prefix+'/enrollment/complete',headers=a['admin'],json={'expected_revision':1}).status_code==200
    assert c.post('/api/v1/admin/ssh-enrollment/begin',headers=a['admin'],json=body).status_code==409
    with main.SessionLocal() as s:assert s.get(Worker,n['id']).enabled


def test_existing_node_identity_is_checked_before_rotating_token(env):
    _,c,a=env;token,slots=node(env)
    n=c.get('/api/v1/admin/nodes',headers=a['admin']).json()[0];prefix='/api/v1/admin/nodes/'+n['id']
    c.post(prefix+'/unmanage',headers=a['admin'],json={'expected_revision':0})
    package=c.get('/api/v1/admin/node-installation-bundle',headers=a['admin']).json()
    body={'node_id':n['id'],'node_name':n['node_name'],'expected_revision':1,'release_id':package['release_id'],'devices':[{'index':0,'uuid':'GPU-wrong-host'}],'operation_id':'b'*32}
    assert c.post('/api/v1/admin/ssh-enrollment/begin',headers=a['admin'],json=body).status_code==409
    assert c.get('/api/v1/admin/nodes',headers=a['admin']).json()[0]['status']=='unmanaged'


@pytest.fixture
def operations(tmp_path,monkeypatch):
    from backend.app.enrollment import bundle
    import base64,hashlib
    payload,release,agent=bundle()
    package={'archive':base64.b64encode(payload).decode(),'sha256':hashlib.sha256(payload).hexdigest(),'release_id':release.id,'agent_digest':agent}
    calls=[]
    class Control:
        def __init__(self,*a):pass
        def call(self,path,body=None):
            calls.append((path,body))
            if path=='admin/nodes':return []
            if path=='admin/health':return {'runner_image':'runner'}
            if path=='admin/node-installation-bundle':return package
            if path=='admin/ssh-enrollment/begin':return {'id':'n','node_name':'ssh-test','token':'SECRET-WORKER','revision':1}
            if path.endswith('/enrollment'):return {'state':'INSTALLING','ready':True,'revision':1,'slots':[],'updater_ready':True}
            return {}
    class Remote:
        def __init__(self,f):self.f=f
        def open(self,authenticate=True):return {'fingerprint':'SHA256:'+'A'*43,'algorithm':'ssh-ed25519'}
        def close(self):pass
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def run(self,argv,*a,**kw):return 0,b'/tmp/mini-eval-node.ABCDEFGH\n',b''
        def upload(self,*a):pass
    monkeypatch.setattr('onboarding.executor.Control',Control)
    ops=Operations(tmp_path/'state','http://test',Remote)
    ops.probe=lambda *a:{'home':'/home/test','python':'/usr/bin/python3','python_version':'3.11','gpus':[{'index':0,'uuid':'GPU-1','name':'GPU'}],'selected_gpus':[0],'configs':[],'services':[],'existing':False,'issues':[],'free_disk_gb':10}
    ops.host_resources=lambda *a:{'ready':True,'verified':True}
    yield ops,calls
    ops.close()


def test_explicit_host_trust_then_confirm_install_and_secret_free_records(operations):
    ops,calls=operations
    op=ops.start('admin',form(),'Bearer SECRET-ADMIN');identity=op['id']
    wait(lambda:ops.jobs[identity]['state']=='NEEDS_HOST_KEY')
    assert not calls
    with pytest.raises(Rejected):ops.trust(identity,'other','SHA256:'+'A'*43)
    with pytest.raises(Rejected):ops.trust(identity,'admin','SHA256:'+'B'*43)
    ops.trust(identity,'admin','SHA256:'+'A'*43)
    wait(lambda:ops.jobs[identity]['state']=='READY')
    assert not any(path.endswith('/begin') for path,_ in calls)
    assert ops.start('admin',form(),'Bearer SECRET-ADMIN')['id']==identity
    ops.confirm(identity,'admin');ops.confirm(identity,'admin')
    wait(lambda:ops.jobs[identity]['state']=='SUCCEEDED')
    assert sum(path.endswith('/begin') for path,_ in calls)==1
    assert identity not in ops.secrets
    assert 'SECRET' not in ''.join(p.read_text() for p in ops.root.glob('*.json'))


def test_timeout_failure_and_expiry_release_credentials(operations):
    ops,_=operations;op=ops.start('admin',form(fingerprint='SHA256:'+'A'*43),'Bearer SECRET')
    wait(lambda:ops.jobs[op['id']]['state']=='READY')
    ops.clock=lambda:time.time()+901
    assert ops.list('admin')['operations'][0]['state']=='EXPIRED'
    assert not ops.secrets
    with pytest.raises(Rejected):ops.confirm(op['id'],'admin')


def test_restart_interrupts_and_never_replays_installation(operations):
    ops,_=operations;op=ops.start('admin',form(),'Bearer SECRET')
    wait(lambda:ops.jobs[op['id']]['state']=='NEEDS_HOST_KEY')
    ops.close()  # A restarted executor cannot race a still-running old writer.
    fresh=Operations(ops.root,'http://test')
    try:
        assert fresh.list('admin')['operations'][0]['state']=='INTERRUPTED'
        assert not fresh.secrets
    finally:fresh.close()


def test_probe_failure_does_not_create_node_and_changed_plan_refuses_install(operations):
    ops,calls=operations;op=ops.start('admin',form(fingerprint='SHA256:'+'A'*43),'Bearer SECRET')
    wait(lambda:ops.jobs[op['id']]['state']=='READY')
    original=ops.probe;ops.probe=lambda *a:{**original(*a),'issues':['execution_journal_pending']}
    ops.confirm(op['id'],'admin');wait(lambda:ops.jobs[op['id']]['state']=='FAILED')
    assert ops.jobs[op['id']]['error']=='environment_changed_repeat_check'
    assert not any(path.endswith('/begin') for path,_ in calls)
    assert not ops.secrets


def test_resource_repair_precedes_install_and_failed_verification_blocks(operations):
    ops,calls=operations
    original=ops.probe
    ops.probe=lambda *a:{**original(*a),'uid':1001,'resource_repair_required':True}
    commands=[]
    factory=ops.remote_factory
    class Remote(factory):
        def run(self,argv,*a,**kw):
            commands.append((argv,kw.get('sudo',False)))
            return super().run(argv,*a,**kw)
    ops.remote_factory=Remote
    ops.host_resources=lambda *a:{'ready':False,'reason':'runtime_limits_not_enforced'}
    op=ops.start('admin',form(fingerprint='SHA256:'+'A'*43),'Bearer SECRET')
    wait(lambda:ops.jobs[op['id']]['state']=='READY')
    ops.confirm(op['id'],'admin')
    wait(lambda:ops.jobs[op['id']]['state']=='FAILED')
    assert ops.jobs[op['id']]['error']=='runtime_limits_not_enforced'
    assert any('configure_node_resources.py' in ' '.join(argv) and sudo for argv,sudo in commands)
    assert any('daemon-reexec' in argv and not sudo for argv,sudo in commands)
    assert not any('install_node.py' in ' '.join(argv) for argv,_ in commands)
    assert not any(path.endswith('/complete') for path,_ in calls)
    assert op['id'] not in ops.secrets

def test_docker_preparation_failure_never_rotates_identity(operations,monkeypatch):
    ops,calls=operations
    def failed(*a):raise Rejected('docker_runtime_verification_failed')
    monkeypatch.setattr('onboarding.docker_prepare.prepare',failed)
    values={**form(fingerprint='SHA256:'+'A'*43),'mode':'docker'}
    op=ops.start('admin',values,'Bearer SECRET')
    wait(lambda:ops.jobs[op['id']]['state']=='READY')
    ops.confirm(op['id'],'admin')
    wait(lambda:ops.jobs[op['id']]['state']=='FAILED')
    assert ops.jobs[op['id']]['error']=='docker_runtime_verification_failed'
    assert not any(path.endswith('/begin') for path,_ in calls)

def test_bundle_includes_trusted_runner_build_inputs():
    import io,zipfile
    from backend.app.enrollment import bundle
    data,*_=bundle()
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        assert 'runner/Dockerfile' in archive.namelist()
        assert 'runner/requirements.txt' in archive.namelist()
