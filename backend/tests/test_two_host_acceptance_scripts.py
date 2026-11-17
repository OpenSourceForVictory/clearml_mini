"""Two-host acceptance drivers: real files/archives, simulated SSH, Docker and API."""
import io
import json
from pathlib import Path
import shlex
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
from scripts import smoke_storage, smoke_image_runtime, smoke_memberships


@pytest.fixture
def two_hosts(tmp_path,monkeypatch):
    remote=tmp_path/'remote';remote.mkdir()
    for name in ('worker','runner','examples/storage-check','examples/image-client'):
        path=tmp_path/name;path.mkdir(parents=True);(path/'fixture.txt').write_text('fixture')
    for key,value in {'EVAL_API_URL':'http://127.0.0.1:18090','STORAGE_TEST_HOST':'127.0.0.1','ADMIN_USERNAME':'fixture','ADMIN_PASSWORD':'fixture-only'}.items():monkeypatch.setenv(key,value)
    output=tmp_path/'evidence.json'
    monkeypatch.setattr('sys.argv',['driver','--remote','fixture.invalid','--identity',str(tmp_path/'unused-key'),'--output',str(output)])
    state={'fault':None,'nodes':[],'configs':{},'jobs':{},'processes':[],'commands':[],'services':[],'requests':[],'output':output,'members':[],'pool_count':0,'seen':0}
    ticks=iter(range(10000));monkeypatch.setattr('time.monotonic',lambda:next(ticks));monkeypatch.setattr('time.sleep',lambda _:None)
    class Service:
        def __init__(self,*args):self.closed=False;state['services'].append(self)
        def serve_forever(self):pass
        def shutdown(self):pass
        def server_close(self):self.closed=True
    class Thread:
        def __init__(self,*args,**kwargs):pass
        def start(self):pass
    for module in (smoke_storage,smoke_image_runtime,smoke_memberships):
        monkeypatch.setattr(module,'ROOT',tmp_path);monkeypatch.setattr(module,'ThreadingHTTPServer',Service);monkeypatch.setattr(module.threading,'Thread',Thread)
    class Process:
        def __init__(self,argv,**kwargs):self.stopped=False;self.handles=[h for h in (kwargs.get('stdout'),kwargs.get('stderr')) if hasattr(h,'close')];state['processes'].append(self)
        def kill(self):self.stopped=True
        def terminate(self):self.stopped=True
        def poll(self):return 0 if self.stopped else None
        def wait(self,timeout):
            for h in self.handles:h.close()
    monkeypatch.setattr('subprocess.Popen',Process)
    def bindings(identity):
        cfg=state['configs'][identity]['config']
        work=(cfg.get('workspace_root') or '/work')+'/runs/attempt/work'
        output='/output'
        mounts=[{'Destination':'/tmp/mini-eval-work','Source':work,'RW':True},{'Destination':'/tmp/mini-eval-output','Source':output,'RW':True}]
        if 'assets' in cfg:
            mounts += [{'Destination':'/data/robotwin','Source':cfg['assets']['robotwin'],'RW':state['fault']=='writable-asset'},
                       {'Destination':'/workspace/eval','Source':work,'RW':True},{'Destination':'/results','Source':output,'RW':True}]
        else:
            mounts += [{'Destination':target,'Source':cfg[key],'RW':state['fault']=='writable-asset'} for target,key in [('/opt/mini-eval/assets','assets_root'),('/opt/mini-eval/cache','cache_root')]]
        return json.dumps([{'Name':'fixture-container','Mounts':mounts,'Config':{'Labels':{'mini-eval.execution':'attempt'}}}])
    def run(argv,**kwargs):
        state['commands'].append(argv)
        remote_call=argv[0]=='ssh';cmd=shlex.split(argv[-1]) if remote_call else argv
        if cmd[:2]==['docker','ps']:return SimpleNamespace(stdout='node1' if remote_call else 'node0',stderr='')
        if cmd[:2]==['docker','inspect']:return SimpleNamespace(stdout=bindings(cmd[2]),stderr='')
        if len(cmd)>2 and cmd[1]=='-c':
            code=cmd[2]
            if 'p.mkdir(); print(p)' in code:return SimpleNamespace(stdout=str(remote),stderr='')
            if 'cfg=json.load(sys.stdin)' in code:
                cfg=json.loads(kwargs['input']);root=Path(cfg['workspace_root']).parent
                (root/'worker.json').write_text(json.dumps(cfg));(root/'worker.json').chmod(0o600)
            if 'print(p.pid)' in code:return SimpleNamespace(stdout='12345',stderr='')
        return SimpleNamespace(stdout='',stderr='')
    monkeypatch.setattr('subprocess.run',run)
    monkeypatch.setattr('subprocess.check_output',lambda argv,**kwargs:run(argv,**kwargs).stdout)
    def api(req,timeout):
        assert urlsplit(req.full_url).netloc=='127.0.0.1:18090'
        path=urlsplit(req.full_url).path.removeprefix('/api/v1/');body=json.loads(req.data) if req.data else None
        state['requests'].append((path,body))
        if path=='auth/login':value={'access_token':'fixture-token'}
        elif path=='admin/pools':
            state['pool_count']+=1;value={'id':'pool'+str(state['pool_count'])}
        elif path=='admin/client-templates':value={'id':'template'}
        elif path=='admin/worker-credentials':
            value={'id':'node'+str(len(state['nodes'])),'node_name':body['node_name'],'token':'fixture-worker'};state['nodes'].append(value)
            state['configs'][value['id']]={'revision':0,'config':None}
            state['members'].append({'id':value['id'],'node_name':value['node_name'],'pool_id':None,'membership_state':'unassigned','membership_revision':0,'last_seen_at':0})
        elif path=='workers':
            state['seen']+=1
            held=any(j['label']=='running-move' and j['reads']<=1 for j in state['jobs'].values())
            for member in state['members']:
                member['last_seen_at']=state['seen']
                if member['membership_state']=='draining' and not held:
                    member['pool_id']=member['desired_pool_id'];member['membership_state']='assigned'
            value=state['members']
        elif path=='admin/resource-memberships':
            held=any(j['label']=='running-move' and j['reads']<=1 for j in state['jobs'].values())
            for member,request in zip(state['members'],body['members']):
                assert request['slot_id']==member['id'] and request['expected_revision']==member['membership_revision']
                member['membership_revision']+=1
                member['desired_pool_id']=body['pool_id']
                if held:member['membership_state']='draining'
                else:member.update(pool_id=body['pool_id'],membership_state='unassigned' if body['pool_id'] is None else 'assigned')
            value=state['members']
        elif path.startswith('admin/nodes/'):
            identity=path.split('/')[2];cfg=state['configs'][identity]
            if path.endswith(('/storage','/runtime')):
                if body is not None:
                    assert body['expected_revision']==cfg['revision'];cfg.update(revision=cfg['revision']+1,config=body['config'])
                missing=cfg['config'] is not None and 'missing-assets' in json.dumps(cfg['config'])
                held=any(j['label']=='old-path' and j['reads']<=1 for j in state['jobs'].values()) and cfg['config'] is not None
                applied=cfg['revision']-1 if held else cfg['revision']
                report={'revision':cfg['revision'],'applied_revision':applied,'effective':cfg['config'],
                        'images':{'mini-eval-image-check:1':{'ready':True}},'assets':{'robotwin':{'ready':not missing}}}
                value={**cfg,'all_applied':not held,'slots':[{'slot_id':identity,'status':'waiting' if held else 'ready','ready':not missing and not held,'report':report}]}
            else:assert body=={'enabled':False};value={}
        elif path=='batches':
            label=next(label for label in ('missing-assets','old-path','new-path','restored','running-move','rejoined') if body['title'].endswith(label))
            cases=[]
            for i in range(2):
                identity='job'+str(len(state['jobs']));state['jobs'][identity]={'label':label,'node':'node'+str(i),'reads':0};cases.append({'job_id':identity})
            value={'id':'batch'+str(len(state['jobs'])),'cases':cases}
        elif path.startswith('jobs/'):
            identity=path.split('/')[1];job=state['jobs'][identity]
            if path.endswith('/runtime-readiness'):value={'ready':False}
            elif path.endswith('/episodes'):value={'progress':{'completed':1 if state['fault']=='episodes' else 2,'successes':2}}
            elif path.endswith('/artifacts'):value=[{'id':str(i),'name':'mounts-'+str(i)} for i in range(2)]
            elif '/artifacts/' in path:value={'marker':('bad-' if state['fault']=='marker' else 'new-' if job['label']=='new-path' else 'old-')+'fixture'}
            else:
                job['reads']+=1
                missing=any('missing-assets' in json.dumps(c['config']) for c in state['configs'].values())
                status='PENDING' if missing else 'RUNNING' if job['reads']==1 and job['label']!='missing-assets' else 'SUCCEEDED'
                if status=='SUCCEEDED' and state['fault']=='failed':status='FAILED'
                attempt={'id':'attempt-'+identity,'slot_id':'same' if state['fault']=='same-slot' else job['node'],'metrics':{}}
                value={'id':identity,'status':status,'attempts':[attempt,attempt] if state['fault']=='retry' else [attempt]}
        else:raise AssertionError(path)
        return io.BytesIO(json.dumps(value).encode())
    monkeypatch.setattr('urllib.request.urlopen',api)
    return state


@pytest.mark.parametrize('module',[smoke_storage,smoke_image_runtime])
@pytest.mark.parametrize('fault',[None,'episodes','marker','failed','same-slot','retry','writable-asset'])
def test_two_host_driver_rejects_invalid_evidence(two_hosts,module,fault):
    state=two_hosts;state['fault']=fault
    if fault:
        with pytest.raises(AssertionError):module.main()
        assert not state['output'].exists()
    else:
        module.main();evidence=json.loads(state['output'].read_text())
        assert len(evidence['hosts'])==2
        assert evidence['original_run_ids']==[r['run_id'] for r in evidence['old_jobs']]
        assert len(evidence['restored_jobs'])==2
        assert len(evidence['batches'])==(4 if module is smoke_image_runtime else 3)
    assert all(p.stopped for p in state['processes'])
    assert all(s.closed for s in state['services'])
    assert len([p for p,b in state['requests'] if b=={'enabled':False}])==2


@pytest.mark.parametrize('fault',[None,'episodes','marker','failed','same-slot','retry'])
def test_membership_driver_tracks_drain_move_remove_and_stale_restart(two_hosts,fault):
    state=two_hosts;state['fault']=fault
    if fault:
        with pytest.raises(AssertionError):smoke_memberships.main()
        assert not state['output'].exists()
    else:
        smoke_memberships.main();evidence=json.loads(state['output'].read_text())
        assert len(evidence['batches'])==2
        assert all(row['pool_id'] is None for row in evidence['unassigned'])
        assert all(row['membership_state']=='draining' for row in evidence['draining'])
        assert all(row['pool_id']=='pool2' for row in evidence['moved'])
        assert all(row['pool_id'] is None for row in evidence['restarted_with_stale_hint'])
        assert len(evidence['rejoined_jobs'])==2
    assert all(p.stopped for p in state['processes'])
    assert all(s.closed for s in state['services'])
    assert len([p for p,b in state['requests'] if b=={'enabled':False}])==2


@pytest.mark.parametrize('module',[smoke_storage,smoke_image_runtime,smoke_memberships])
def test_two_host_driver_rejects_nonisolated_api_before_listening(two_hosts,module,monkeypatch):
    monkeypatch.setenv('EVAL_API_URL','http://192.0.2.1:18000')
    with pytest.raises(ValueError,match='isolated local API'):module.main()
    assert not two_hosts['services']
    assert not two_hosts['requests'] and not two_hosts['commands'] and not two_hosts['processes']
