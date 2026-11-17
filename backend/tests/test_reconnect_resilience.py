import copy
import subprocess
import threading
import pytest
from onboarding import executor
from onboarding.contracts import Rejected
from worker import probe_dependencies
from backend.tests.test_ssh_onboarding import operations,form,wait
from backend.tests.test_execution import env


def test_dependency_preparation_never_blocks_caller(monkeypatch):
    entered=threading.Event();release=threading.Event()
    def slow():entered.set();assert release.wait(5)
    monkeypatch.setattr(probe_dependencies,'ensure',slow)
    thread=probe_dependencies.start()
    try:
        assert entered.wait(2) and thread.is_alive()
    finally:release.set();thread.join(2)
    assert not thread.is_alive()


@pytest.mark.parametrize('error,reason',[(PermissionError('SECRET'),'permission_denied'),(RuntimeError('SECRET'),'preparation_failed'),(OSError('SECRET'),'preparation_failed'),(subprocess.TimeoutExpired(['SECRET'],120),'pip_install_timeout'),(subprocess.CalledProcessError(1,['SECRET']),'pip_install_failed')])
def test_dependency_failure_is_classified_without_secrets(monkeypatch,caplog,error,reason):
    def fail():raise error
    monkeypatch.setattr(probe_dependencies,'ensure',fail)
    thread=probe_dependencies.start();thread.join(2)
    assert reason in caplog.text and 'SECRET' not in caplog.text


@pytest.mark.parametrize('value',['0','29','1801','bad'])
def test_timeout_config_rejects_invalid(tmp_path,monkeypatch,value):
    monkeypatch.setenv('ONBOARDING_RECONNECT_TIMEOUT_SECONDS',value)
    with pytest.raises(ValueError):executor.Operations(tmp_path,'http://api')


@pytest.mark.parametrize('value',[None,'30','1800'])
def test_timeout_config(tmp_path,monkeypatch,value):
    if value is None:monkeypatch.delenv('ONBOARDING_RECONNECT_TIMEOUT_SECONDS',raising=False)
    else:monkeypatch.setenv('ONBOARDING_RECONNECT_TIMEOUT_SECONDS',value)
    ops=executor.Operations(tmp_path,'http://api')
    try:assert ops.reconnect_timeout==int(value or 300)
    finally:ops.close()


def ready_job(ops):
    job=ops.start('admin',form(fingerprint='SHA256:'+'A'*43),'old')
    identity=job['id'];wait(lambda:ops.jobs[identity]['state']=='READY')
    ops.update(identity,'FAILED',node_id='n',installation_revision=1,verification_available=True)
    return identity


@pytest.mark.parametrize('mode',['ready','complete','changed','wrong-release','timeout','unauthorized'])
def test_resume_verification_without_ssh_or_rotation(operations,monkeypatch,mode):
    ops,calls=operations;identity=ready_job(ops)
    status={'state':'INSTALLING','ready':True,'revision':1,'release_id':ops.jobs[identity]['release_id']}
    if mode=='complete':status.update(state='COMPLETE',ready=False,revision=2)
    if mode=='changed':status['revision']=3
    if mode=='wrong-release':status['release_id']='other'
    if mode=='timeout':status['ready']=False
    events=[]
    class Control:
        def __init__(self,url,authorization):assert authorization=='fresh'
        def call(self,path,body=None):
            events.append((path,body))
            if mode=='unauthorized':raise Rejected('control_api_rejected_401')
            return copy.deepcopy(status)
    monkeypatch.setattr(executor,'Control',Control)
    def no_ssh(*args):raise AssertionError('retry must not use SSH')
    ops.remote_factory=no_ssh
    if mode=='timeout':
        ticks=iter([0,1,301]);monkeypatch.setattr(executor.time,'monotonic',lambda:next(ticks))
        monkeypatch.setattr(executor.time,'sleep',lambda _:None)
    # Execute synchronously to keep the fake clock local to this assertion.
    monkeypatch.setattr(ops.pool,'submit',lambda fn,*args:fn(*args))
    result=ops.retry_verification(identity,'admin','fresh')
    if mode in ('ready','complete'):assert result['state']=='SUCCEEDED'
    else:
        expected={'changed':'installation_changed_repeat_check','wrong-release':'installation_changed_repeat_check','timeout':'component_reconnect_timeout','unauthorized':'control_api_rejected_401'}
        assert result['state']=='FAILED' and result['error']==expected[mode]
    assert not any('begin' in p for p,b in events)
    assert sum(p.endswith('/complete') for p,b in events)==(mode=='ready')
    assert 'fresh' not in (ops.root/(identity+'.json')).read_text()


def test_resume_owner_kind_concurrency_and_idempotency(operations,monkeypatch):
    ops,_=operations;identity=ready_job(ops)
    with pytest.raises(Rejected,match='operation_not_found'):ops.retry_verification(identity,'other','fresh')
    ops.jobs[identity]['kind']='cleanup'
    with pytest.raises(Rejected,match='verification_not_available'):ops.retry_verification(identity,'admin','fresh')
    ops.jobs[identity]['kind']='enrollment'
    ops.jobs['other']={**copy.deepcopy(ops.jobs[identity]),'id':'other','state':'VERIFYING'}
    with pytest.raises(Rejected,match='target_has_active_operation'):ops.retry_verification(identity,'admin','fresh')
    del ops.jobs['other']
    ops.update(identity,'READY')
    with pytest.raises(Rejected,match='verification_not_available'):ops.retry_verification(identity,'admin','fresh')
    ops.update(identity,'VERIFYING')
    assert ops.retry_verification(identity,'admin','fresh')['state']=='VERIFYING'


def test_optional_probe_capability_does_not_block_enrollment(env):
    from backend.tests.test_enrollment import request,connect,connect_agent
    from worker.version import SOFTWARE_VERSION,CAPABILITIES
    from backend.app.worker_upgrades import installation_capabilities
    _,c,a=env;path,cred,old,ids=request(env,count=1)
    token=connect(env,cred,[0])
    caps=[x for x in CAPABILITIES if x!='service_readiness_v1']
    software={'version':SOFTWARE_VERSION,'build_digest':cred['release_id'],'capabilities':caps}
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':ids[0],'software':software}).status_code==200
    package=c.get('/api/v1/admin/node-installation-bundle',headers=a['admin']).json()
    c.post('/api/v1/worker/upgrades/poll',headers=token,json={'agent_digest':package['agent_digest']})
    connect_agent(c,token,cred['release_id'])
    status=c.get(path+'/enrollment',headers=a['admin']).json()
    assert status['ready'] and not status['slots'][0]['probe_ready']
    assert not installation_capabilities(caps[1:],CAPABILITIES)
    software['capabilities']=CAPABILITIES
    c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':ids[0],'software':software})
    assert c.get(path+'/enrollment',headers=a['admin']).json()['slots'][0]['probe_ready']


def test_retry_endpoint_uses_current_auth(env,monkeypatch):
    from backend.app import ssh_onboarding
    _,c,a=env;seen=[]
    monkeypatch.setattr(ssh_onboarding,'executor',lambda path,body:seen.append((path,body)) or {'state':'VERIFYING'})
    assert c.post('/api/v1/admin/ssh-onboarding/op/verify',headers=a['alice']).status_code==403
    assert c.post('/api/v1/admin/ssh-onboarding/op/verify',headers=a['admin']).status_code==200
    assert seen[0][0]=='/verify' and seen[0][1]['authorization']==a['admin']['Authorization']


@pytest.mark.parametrize('stderr,reason',[(b'Permission denied SECRET','permission_denied'),('No space left SECRET','disk_full'),(b'CERTIFICATE_VERIFY_FAILED SECRET','tls_certificate_failed'),(b'ProxyError SECRET','proxy_failed'),(b'Temporary failure in name resolution SECRET','dns_failed'),(b'ReadTimeout SECRET','network_timeout'),(b'No matching distribution SECRET','package_or_python_incompatible'),(None,'pip_install_failed')])
def test_pip_failure_safe_categories(stderr,reason):
    assert probe_dependencies.pip_failure(stderr)==reason


def test_resume_after_executor_restart(operations,monkeypatch):
    ops,_=operations;identity=ready_job(ops)
    ops.update(identity,'VERIFYING','RECONNECT')
    restored=executor.Operations(ops.root,ops.api_url)
    try:
        assert restored.jobs[identity]['state']=='INTERRUPTED' and not restored.secrets
        seen=[];monkeypatch.setattr(restored.pool,'submit',lambda fn,*args:seen.append(args))
        assert restored.retry_verification(identity,'admin','fresh')['state']=='VERIFYING'
        assert seen==[(identity,'fresh')]
    finally:restored.close()
