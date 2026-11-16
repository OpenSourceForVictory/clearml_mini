import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from scripts.fleet_release import Checkpoints, TARGETS, atomic_json, probe, release_plan, verify_identity


def facts(host):
    return {'addresses': host, 'hostname': 'node', 'machine_id': host,
            'worker': {'SOFTWARE_VERSION': '0.10.0', 'PROTOCOL_VERSION': 9},
            'agent': {'VERSION': '1.0.0'}, 'mount': 'nfs4', 'gpus': 'GPU-' + host,
            'services': {x: 'ActiveState=active\nSubState=running' for x in
                         ('mini-eval-worker-nfs', 'mini-eval-agent', 'mini-eval-updater')}}


def observed(host):
    return probe(host, run=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(facts(host))))


def test_no_outside_target_execution():
    def never(*a, **kw): pytest.fail('out of scope command executed')
    with pytest.raises(ValueError): probe('192.168.8.174', run=never)


def test_strict_ssh_and_allowlisted_output():
    def run(argv, **kwargs):
        assert 'StrictHostKeyChecking=yes' in argv
        assert 'BatchMode=yes' in argv
        assert kwargs['timeout'] == 55
        return SimpleNamespace(returncode=0, stdout=json.dumps({**facts('192.168.8.157'), 'token': 'secret'}))
    result = probe('192.168.8.157', run)
    assert result['status'] == 'observed'
    assert 'secret' not in json.dumps(result)


def test_wrong_host_rejected():
    result = probe('192.168.8.157', lambda *a, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(facts('192.168.8.174'))))
    assert result['status'] == 'unknown'
    assert result['error'] == 'address_identity_mismatch'


def test_failures_not_dumped():
    result = probe('192.168.8.157', lambda *a, **kw: SimpleNamespace(returncode=255, stdout='secret', stderr='secret'))
    assert result['status'] == 'unknown'
    assert 'secret' not in json.dumps(result)


def test_partial_plan_rejected():
    with pytest.raises(ValueError): release_plan({'nodes': [observed('192.168.8.157')]})


def test_plan_and_changed_identity():
    plan = release_plan({'observed_at': 'now', 'nodes': [observed(h) for h in TARGETS]})
    from worker.version import PROTOCOL_VERSION
    assert plan['target_protocol'] == PROTOCOL_VERSION
    expected = plan['targets'][0]
    verify_identity(expected, observed(expected['address']))
    with pytest.raises(ValueError): verify_identity(expected, {**observed(expected['address']), 'machine_id': 'other'})


def test_private_atomic_file(tmp_path):
    p = tmp_path / 'state.json'
    atomic_json(p, {'ok': True})
    assert p.stat().st_mode & 0o777 == 0o600
    atomic_json(p, {'ok': False})
    assert json.loads(p.read_text()) == {'ok': False}


def test_checkpoints_idempotent_and_input_bound(tmp_path):
    with Checkpoints(tmp_path, 'one').locked() as journal:
        assert journal.begin('inventory', {'nodes': list(TARGETS)})
        journal.finish('inventory')
    with Checkpoints(tmp_path, 'one').locked() as journal:
        assert not journal.begin('inventory', {'nodes': list(TARGETS)})
        with pytest.raises(ValueError): journal.begin('inventory', {'nodes': []})


def test_uncertain_step_is_not_repeated(tmp_path):
    with Checkpoints(tmp_path, 'one').locked() as journal:
        journal.begin('upgrade', {'target': '192.168.8.157'})
    with Checkpoints(tmp_path, 'one').locked() as journal:
        with pytest.raises(RuntimeError): journal.begin('upgrade', {'target': '192.168.8.157'})


def test_different_releases_share_lock(tmp_path):
    with Checkpoints(tmp_path, 'one').locked():
        with pytest.raises(BlockingIOError):
            with Checkpoints(tmp_path, 'two').locked(): pytest.fail('lock bypassed')


def test_no_path_traversal(tmp_path):
    with pytest.raises(ValueError): Checkpoints(tmp_path, '../other')


def fake_api(enabled=True, held=0):
    node = {'id': 'node', 'node_name': TARGETS['192.168.8.157'], 'enabled': enabled,
            'management_revision': 0, 'held_executions': held}
    calls = []
    def api(path, body=None, method=None):
        if body is None: return [dict(node)]
        calls.append(body)
        assert body['expected_revision'] == node['management_revision']
        node.update(enabled=body['enabled'], management_revision=node['management_revision']+1)
        return dict(node)
    plan = {'targets': [{'address': '192.168.8.157', 'node_name': node['node_name']}]}
    return api, node, calls, plan


def test_drain_restore_owned_changes(tmp_path):
    from scripts.fleet_release_api import drain, restore_admission
    api, node, calls, plan = fake_api()
    with Checkpoints(tmp_path, 'one').locked() as j:
        drain(api, plan, j)
        assert not node['enabled']
        restore_admission(api, plan, j)
        restore_admission(api, plan, j)
        assert node['enabled'] and len(calls) == 2


def test_preserve_preexisting_disabled_node(tmp_path):
    from scripts.fleet_release_api import drain, restore_admission
    api, node, calls, plan = fake_api(enabled=False)
    with Checkpoints(tmp_path, 'one').locked() as j:
        drain(api, plan, j)
        restore_admission(api, plan, j)
    assert not node['enabled'] and not calls


def test_drain_timeout_retains_task_and_can_restore(tmp_path):
    from scripts.fleet_release_api import drain, restore_admission
    api, node, calls, plan = fake_api(held=1)
    with Checkpoints(tmp_path, 'one').locked() as j:
        with pytest.raises(TimeoutError): drain(api, plan, j, timeout=0)
        assert node['held_executions'] == 1
        restore_admission(api, plan, j)
        assert node['enabled']


def test_restore_does_not_overwrite_admin_change(tmp_path):
    from scripts.fleet_release_api import drain, restore_admission
    api, node, calls, plan = fake_api()
    with Checkpoints(tmp_path, 'one').locked() as j:
        drain(api, plan, j)
        node['management_revision'] += 1
        with pytest.raises(RuntimeError): restore_admission(api, plan, j)
    assert not node['enabled'] and len(calls) == 1


def test_lost_patch_response_not_retried(tmp_path):
    from scripts.fleet_release_api import drain, restore_admission
    api, node, calls, plan = fake_api()
    def uncertain(path, body=None, method=None):
        response = api(path, body, method)
        if body is not None: raise RuntimeError('connection lost after mutation')
        return response
    with Checkpoints(tmp_path, 'one').locked() as j:
        with pytest.raises(RuntimeError): drain(uncertain, plan, j)
        with pytest.raises(RuntimeError): drain(uncertain, plan, j)
        with pytest.raises(RuntimeError): restore_admission(api, plan, j)
    assert len(calls) == 1


from backend.tests.test_execution import env


def test_real_api_drain_keeps_lease_and_blocks_new_claim(env, tmp_path):
    from backend.tests.test_gpu_sharing import setup_gpu, submit_gpu
    from backend.app.scheduling import tick
    from backend.app.core import Worker, Slot
    from scripts.fleet_release_api import drain, restore_admission
    main, client, auth = env
    pool, slots, tokens = setup_gpu(env, 1)
    submit_gpu(env, pool)
    tick(main.SessionLocal)
    task = client.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=tokens[0], json={'known_execution_ids': []}).json()['task']
    with main.SessionLocal() as session:
        w = session.get(Worker, session.get(Slot, slots[0]).worker_id)
        w.node_name = TARGETS['192.168.8.157']; session.commit()
    def api(path, body=None, method=None):
        response = client.request(method or 'GET', '/api/v1/' + path, headers=auth['admin'], json=body)
        assert response.status_code == 200, response.text
        return response.json()
    plan = {'targets': [{'address': '192.168.8.157', 'node_name': TARGETS['192.168.8.157']}]}
    with Checkpoints(tmp_path, 'one').locked() as j:
        with pytest.raises(TimeoutError): drain(api, plan, j, timeout=0)
        heartbeat = client.post('/api/v1/worker/heartbeat', headers=tokens[0], json={'slot_id': slots[0], 'execution_id': task['execution_id'], 'started': True})
        assert heartbeat.status_code == 200 and heartbeat.json()['cancel'] is False
        claim = client.post(f'/api/v1/worker/slots/{slots[0]}/claim', headers=tokens[0], json={'known_execution_ids': [task['execution_id']]})
        assert claim.status_code == 200 and claim.json()['task'] is None
        restore_admission(api, plan, j)


def test_compatibility_gate_rejects_old_worker():
    from scripts.fleet_release_api import assert_release_ready
    api, node, _, plan = fake_api()
    plan.update(target_protocol=10, target_worker='0.12.0', target_agent='1.1.0')
    def incompatible(path, *args):
        if path.endswith('/upgrades'):
            return {'slots': [{'online': True, 'protocol_version': 9, 'software': {'version': '0.10.0'}}]}
        return api(path, *args)
    with pytest.raises(RuntimeError, match='worker release is not ready'):
        assert_release_ready(incompatible, plan, 'digest')


def test_compose_keeps_lan_overlay(tmp_path):
    from scripts.fleet_release_control import compose_command
    command = compose_command(tmp_path/'env', tmp_path/'image.yml')
    files = [command[i+1] for i, value in enumerate(command) if value == '-f']
    assert len(files) == 3
    assert files[0].endswith('/deploy/compose.yml')
    assert files[1].endswith('/deploy/lan-compose.yml')


def test_invalid_backup_never_starts_recovery(tmp_path):
    from scripts.fleet_release_control import restore_probe
    backup = tmp_path/'db.dump'; backup.write_bytes(b'bad backup')
    def never(*a, **kw): pytest.fail('recovery started for invalid backup')
    with pytest.raises(ValueError): restore_probe(backup, 'incorrect', 'image', run=never)


def test_upgrade_requires_drain(tmp_path):
    from scripts.fleet_release_api import upgrade_nodes
    api, node, calls, plan = fake_api()
    with Checkpoints(tmp_path, 'one').locked() as j:
        with pytest.raises(RuntimeError, match='drained'): upgrade_nodes(api, plan, j, 'release')
    assert not calls


def test_upgrade_failure_does_not_touch_next_node(tmp_path):
    from scripts.fleet_release_api import upgrade_nodes
    nodes = [{'id': str(i), 'node_name': name, 'enabled': False, 'held_executions': 0}
             for i, name in enumerate(TARGETS.values())]
    calls = []
    def api(path, body=None, method=None):
        if path == 'admin/nodes': return nodes
        if body:
            calls.append(path)
            return {'id': 'upgrade'}
        return {'revision': 0, 'updater_online': True, 'history': [{'id': 'upgrade', 'status': 'FAILED'}]}
    plan = {'targets': [{'address': h, 'node_name': n} for h,n in TARGETS.items()]}
    with Checkpoints(tmp_path, 'one').locked() as j:
        with pytest.raises(RuntimeError, match='later nodes'): upgrade_nodes(api, plan, j, 'release')
    assert len(calls) == 1


def test_acceptance_registry_survives_restart_and_refuses_other_resources(tmp_path):
    from scripts.fleet_acceptance import Registry
    path = tmp_path/'registry.json'
    reg = Registry(path, 'test-run')
    reg.register('batch', 'one', 'alice', 'test-run-batch')
    again = Registry(path, 'test-run')
    assert again.resource('batch', 'one', 'alice')['name'] == 'test-run-batch'
    with pytest.raises(ValueError): again.resource('batch', 'one', 'bob')
    with pytest.raises(ValueError): again.resource('batch', 'unknown', 'alice')
    with pytest.raises(ValueError): again.register('batch', 'other', 'alice', 'production-batch')


def test_cleanup_validates_remote_identity_before_mutation(tmp_path):
    from scripts.fleet_acceptance import Registry
    reg = Registry(tmp_path/'registry.json', 'test-run')
    reg.register('batch', 'one', 'alice', 'test-run-batch')
    def api(path, body=None, method=None):
        assert body is None, 'unexpected mutation'
        return {'title': 'production-batch', 'control_revision': 0}
    with pytest.raises(ValueError): reg.cancel_batch(api, 'one', 'alice')


def test_disabled_test_template_cleanup_is_idempotent(tmp_path):
    from scripts.fleet_acceptance import Registry
    reg = Registry(tmp_path/'registry.json', 'test-run')
    reg.register('server-template', 'one', 'admin', 'test-run-server')
    record = {'id': 'one', 'name': 'test-run-server', 'enabled': True}
    changes = []
    def api(path, body=None, method=None):
        if body is None: return [dict(record)]
        changes.append(body); record['enabled'] = body['enabled']
    reg.disable_template(api, 'server', 'one', 'admin')
    reg.disable_template(api, 'server', 'one', 'admin')
    assert len(changes) == 1


def test_acceptance_resubmits_same_key_after_lost_response(tmp_path):
    from scripts.smoke_fleet_orchestration import Acceptance
    calls=[]
    def api(path,body=None,method=None,idempotency_key=None):
        calls.append((body,idempotency_key))
        if len(calls)==1:raise RuntimeError('response lost')
        return {'id':'batch'}
    acceptance=Acceptance(api,tmp_path,'fleet-test')
    with pytest.raises(RuntimeError):acceptance.submit('case',{'cases':[]})
    recovered=Acceptance(api,tmp_path,'fleet-test')
    assert recovered.submit('case',{'cases':[]})=='batch'
    assert calls[0]==calls[1]
    assert recovered.registry.resource('batch','batch','admin')
    with pytest.raises(ValueError):recovered.submit('case',{'cases':[{}]})


def test_fleet_business_programs_are_bounded_and_syntax_valid():
    from scripts.fleet_probe_programs import SERVER,CLIENT,COMMAND
    for source in (SERVER,CLIENT,COMMAND):
        assert len(source)<2000
        compile(source,'fleet-probe','exec')


def test_upgrade_waits_for_agent_config_after_success(tmp_path,monkeypatch):
    import scripts.fleet_release_api as release
    api,node,_,plan=fake_api(enabled=False)
    plan.update(target_protocol=10,target_worker='0.12.0',target_agent='1.1.0')
    calls=[];checks=[]
    def upgraded(path,body=None,method=None):
        if path=='admin/nodes':return [node]
        if body:calls.append(body);return {'id':'upgrade'}
        return {'revision':0,'updater_online':True,'history':[{'id':'upgrade','status':'SUCCEEDED'}]}
    def ready(*args):
        checks.append(True)
        if len(checks)==1:raise RuntimeError('Agent config heartbeat pending')
    monkeypatch.setattr(release,'assert_release_ready',ready)
    with Checkpoints(tmp_path,'one').locked() as j:
        release.upgrade_nodes(upgraded,plan,j,'release',sleep=lambda _:None)
        assert j.state['steps']['upgrade:'+node['node_name']]['status']=='done'
    assert len(calls)==1 and len(checks)==2


def test_reconcile_only_exact_successful_upgrade(tmp_path,monkeypatch):
    import scripts.fleet_release_api as release
    api,node,_,plan=fake_api(enabled=False)
    operation={'id':'known','status':'SUCCEEDED','release_id':'wrong'}
    def current(path,*args):
        return {'history':[operation]} if path.endswith('/upgrades') else api(path,*args)
    monkeypatch.setattr(release,'assert_release_ready',lambda *args:None)
    with Checkpoints(tmp_path,'one').locked() as j:
        step='upgrade:'+node['node_name'];j.begin(step,{'release_id':'release'})
        j.state['steps'][step]['upgrade_id']='known'
        with pytest.raises(RuntimeError):release.reconcile_upgrade(current,plan,j,node['node_name'],'release')
        operation['release_id']='release'
        release.reconcile_upgrade(current,plan,j,node['node_name'],'release')
        assert j.state['steps'][step]['status']=='done'


def test_echo_business_command_writes_sdk_result(tmp_path):
    import os,subprocess,sys,threading
    from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
    from scripts.fleet_probe_programs import CLIENT
    class Echo(BaseHTTPRequestHandler):
        def do_POST(self):
            value=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(200);self.end_headers()
            self.wfile.write(json.dumps({'received':value,'model':'fleet-test-model','server_execution':'server-id'}).encode())
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Echo)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    source=tmp_path/'input.json';result=tmp_path/'result.json'
    source.write_text(json.dumps({'task':{'case_id':'0'},'platform':{'execution_id':'client-id','service':{'endpoint':'http://127.0.0.1:'+str(server.server_port)}},'business':{'variant':0,'sleep_seconds':0}}))
    try:
        output=subprocess.run([sys.executable,'-c',CLIENT],env={**os.environ,'MINI_EVAL_INPUT':str(source),'MINI_EVAL_RESULT':str(result)},capture_output=True,text=True,timeout=10,check=True)
        assert json.loads(result.read_text())=={'success':True,'metrics':{'echo_verified':1}}
        assert json.loads(output.stdout)['server_execution']=='server-id'
    finally:
        server.shutdown();server.server_close();thread.join(2)


def test_failed_backup_removes_partial_and_does_not_replace_old_file(tmp_path):
    from scripts.fleet_release_control import backup_database
    path=tmp_path/'backup.dump'
    def failed(*args,**kwargs):
        kwargs['stdout'].write(b'incomplete')
        return SimpleNamespace(returncode=1)
    with pytest.raises(RuntimeError):backup_database(path,run=failed)
    assert not path.exists() and not path.with_suffix('.manifest.json').exists()
    path.write_bytes(b'previous validated backup')
    with pytest.raises(FileExistsError):backup_database(path,run=failed)
    assert path.read_bytes()==b'previous validated backup'


def test_worker_smoke_reads_matching_multi_execution_journal(tmp_path):
    from scripts.smoke_worker import journal_for
    state=tmp_path/'state.json';state.write_text('null')
    directory=tmp_path/'state.json.runs';directory.mkdir()
    (directory/'one.json').write_text(json.dumps({'task':{'job_id':'one','execution_id':'first'}}))
    (directory/'two.json').write_text(json.dumps({'task':{'job_id':'two','execution_id':'second'}}))
    path,value=journal_for(state,'two')
    assert path.name=='two.json' and value['task']['execution_id']=='second'
    with pytest.raises(RuntimeError):journal_for(state,'missing')


def test_interrupted_setup_stops_before_other_node_changes(tmp_path):
    from scripts.smoke_fleet_orchestration import Acceptance
    def never(*a,**kw):pytest.fail('interrupted setup touched platform')
    a=Acceptance(never,tmp_path,'fleet-test');a.state['nodes']['node']={'stage':'preparing'};a.save()
    with pytest.raises(RuntimeError,match='reconciliation'):a.prepare()


def test_fleet_input_modes_keep_command_episode_contract(tmp_path):
    from scripts.smoke_fleet_orchestration import Acceptance
    a=Acceptance(None,tmp_path,'fleet-test')
    a.state['nodes']={'rtx4090-157':{'pool_id':'pool','slot_id':'server'}}
    a.state['templates']={'command-docker':'command','client-docker':'client','server-docker':'server'}
    command=a.body(command=True)
    assert all(c['episodes']==1 for c in command['cases'])
    assert command['client']['service_mode']=='none' and 'services' not in command
    managed=a.body()
    assert all(c['episodes']==2 for c in managed['cases'])
    assert managed['client']['service_mode']=='managed' and managed['services'][0]['slot_id']=='server'


def test_rollout_completion_requires_all_nodes_and_restore(tmp_path):
    from scripts.fleet_rollout import complete
    plan={'targets':[{'node_name':n} for n in TARGETS.values()]}
    with Checkpoints(tmp_path,'one').locked() as journal:
        assert not complete(journal,plan)
        for step in ['backup','control']+[prefix+n for n in TARGETS.values() for prefix in ('drain:','upgrade:')]:
            journal.begin(step,{});journal.finish(step)
        record=journal.state['steps']['drain:'+next(iter(TARGETS.values()))]
        record.update(was_enabled=True,restored=False)
        assert not complete(journal,plan)
        record['restored']=True
        assert complete(journal,plan)


def test_rollout_environment_guard_does_not_expose_secrets(monkeypatch):
    import scripts.fleet_rollout as rollout
    current={'Config':{'Env':['JWT_SECRET=secret']},'HostConfig':{'PortBindings':{}}}
    monkeypatch.setattr(rollout,'run',lambda _:json.dumps({'services':{'api':{'environment':{'JWT_SECRET':'changed-secret'},'ports':[]}}}))
    with pytest.raises(RuntimeError) as e:rollout.check_compose_environment([],current)
    assert 'secret' not in str(e.value)


def test_release_gate_includes_worker_configuration_receipts():
    from scripts.fleet_release_api import assert_release_ready
    api,node,_,plan=fake_api(enabled=False)
    plan.update(target_protocol=10,target_worker='0.12.0',target_agent='1.1.0')
    node['connection']={'agent':{'status':'online','version':'1.1.0','digest':'release','config_status':'applied'}}
    ready={'storage':False}
    def current(path,*args):
        if path.endswith('/upgrades'):return {'slots':[{'online':True,'protocol_version':10,'software':{'version':'0.12.0','build_digest':'release'}}]}
        if path.endswith('/storage'):return {'all_applied':ready['storage']}
        if path.endswith('/runtime'):return {'slots':[{'ready':True}]}
        if path.endswith('/host-environments'):return {'environments':{}}
        return api(path,*args)
    with pytest.raises(RuntimeError,match='storage/runtime'):assert_release_ready(current,plan,'release')
    ready['storage']=True
    assert_release_ready(current,plan,'release')


def test_plan_rejects_ambiguous_migration_heads(tmp_path,monkeypatch):
    import scripts.fleet_release as release
    versions=tmp_path/'backend/alembic/versions';versions.mkdir(parents=True)
    (versions/'first.py').write_text("revision: str='first'\ndown_revision=None\n")
    (versions/'second.py').write_text("revision='second'\ndown_revision='first'\n")
    monkeypatch.setattr(release,'ROOT',tmp_path)
    assert release.migration_head()=='second'
    (versions/'branch.py').write_text("revision='branch'\ndown_revision='first'\n")
    with pytest.raises(ValueError,match='single migration head'):release.migration_head()


def test_release_rejects_image_migration_or_protocol_mismatch(monkeypatch):
    import scripts.fleet_rollout as rollout
    calls=[]
    def wrong(argv):
        calls.append(argv)
        return json.dumps({'worker':'0.12.0','protocol':9,'agent':'1.1.0','heads':['n407c89']})
    monkeypatch.setattr(rollout,'run',wrong)
    plan={'target_worker':'0.12.0','target_protocol':10,'target_agent':'1.1.0','target_database':'r841a23'}
    with pytest.raises(RuntimeError,match='incompatible'):rollout.check_image('image:1',plan)
    assert len(calls)==1 and calls[0][:5]==['docker','run','--rm','--network','none']

@pytest.mark.parametrize('change',['environment','mount','unchanged'])
def test_rollout_preserves_onboarding_socket(monkeypatch,change):
    import scripts.fleet_rollout as rollout
    env={'ONBOARDING_SOCKET':'/run/onboarding/control.sock'}
    current={'Config':{'Env':[k+'='+v for k,v in env.items()]},'HostConfig':{'PortBindings':{}},'Mounts':[{'Source':'/host/socket','Destination':'/run/onboarding'}]}
    desired={'environment':dict(env),'ports':[],'volumes':[{'type':'bind','source':'/host/socket','target':'/run/onboarding'}]}
    if change=='environment':desired['environment']={}
    if change=='mount':desired['volumes']=[]
    monkeypatch.setattr(rollout,'run',lambda _:json.dumps({'services':{'api':desired}}))
    if change=='unchanged':rollout.check_compose_environment([],current)
    else:
        with pytest.raises(RuntimeError):rollout.check_compose_environment([],current)
