"""Persistent acceptance setup/restore checks against a stateful in-memory control boundary."""
import copy
import json

import pytest
from scripts.smoke_fleet_orchestration import Acceptance, TARGETS, PROTOCOL_VERSION, IMAGE


@pytest.fixture
def fleet(tmp_path):
    nodes=[{'id':str(i),'node_name':name,'roles':['client'],'held_executions':0,'management_revision':0} for i,name in enumerate(TARGETS.values())]
    slots=[{'id':'slot'+n['id'],'worker_id':n['id'],'pool_id':'pool','node_name':n['node_name'],'online':True,'protocol_version':PROTOCOL_VERSION} for n in nodes]
    configs={n['id']:{'host-environments':{'revision':0,'environments':{'original':{'python':'/usr/bin/python3','code_root':'/original'}}},
                         'runtime':{'revision':0,'config':{'images':['original:1'],'assets':{'dataset':'/data'}}},
                         'service-network':{'revision':0,'config':None}} for n in nodes}
    original=copy.deepcopy(configs);templates=[];calls=[];fault={'path':None}
    def api(path,body=None,method=None,**kwargs):
        calls.append((path,copy.deepcopy(body),method))
        if body is not None and path==fault['path']:raise RuntimeError('injected transport failure')
        if path=='admin/nodes':return copy.deepcopy(nodes)
        if path=='workers':return copy.deepcopy(slots)
        if path in ('admin/server-templates','client-templates','admin/client-templates'):
            if body is not None:
                row={**body,'id':'template'+str(len(templates)),'enabled':True,'revision':0};templates.append(row);return copy.deepcopy(row)
            return copy.deepcopy([t for t in templates if ('startup_timeout' in t)==(path=='admin/server-templates')])
        parts=path.split('/')
        if len(parts)>=3 and parts[1].endswith('-templates'):
            row=next(t for t in templates if t['id']==parts[2]);row.update(body);return copy.deepcopy(row)
        if path.startswith('admin/nodes/'):
            identity=parts[2];node=next(n for n in nodes if n['id']==identity)
            if len(parts)==3:
                assert body['expected_revision']==node['management_revision']
                node.update(roles=body['roles'],management_revision=node['management_revision']+1);return copy.deepcopy(node)
            key=parts[3];current=configs[identity][key]
            if body is not None:
                assert body['expected_revision']==current['revision']
                current.update({k:v for k,v in body.items() if k!='expected_revision'});current['revision']+=1
            result=copy.deepcopy(current)
            result['slots']=[{'ready':True,'report':{'applied_revision':current['revision']}}]
            return result
        raise AssertionError(path)
    return Acceptance(api,tmp_path,'fixture-run'),nodes,slots,configs,original,templates,calls,fault


def test_prepare_reentry_and_restore_preserve_operator_settings(fleet):
    a,nodes,slots,configs,original,templates,calls,_=fleet
    a.prepare()
    assert len(a.state['nodes'])==len(TARGETS) and len(templates)==6
    for node in nodes:
        cfg=configs[node['id']]
        assert cfg['runtime']['config']['assets']=={'dataset':'/data'}
        assert set(cfg['runtime']['config']['images'])=={'original:1',IMAGE}
        assert 'original' in cfg['host-environments']['environments']
    assert {t['execution_mode'] for t in templates}=={'host','docker'}
    assert sum(t.get('task_type')=='command' for t in templates)==2
    assert sum(t.get('integration_mode')=='sdk-command' for t in templates)==2
    mutations=lambda:sum(body is not None for _,body,_ in calls)
    before=mutations();recovered=Acceptance(a.api,a.directory,a.run_id);recovered.prepare()
    assert mutations()==before
    recovered.cleanup()
    for identity,values in configs.items():
        for key,value in values.items():
            field='environments' if key=='host-environments' else 'config'
            assert value[field]==original[identity][key][field]
    assert all(n['roles']==['client'] for n in nodes)
    assert not any(t['enabled'] for t in templates)
    before=mutations();recovered.cleanup();assert mutations()==before
    assert recovered.registry.state['evidence'][-1]['scenario']=='cleanup'


@pytest.mark.parametrize('fault',['busy','offline','protocol','transport'])
def test_prepare_rejects_unready_and_retains_interrupted_state(fleet,fault):
    a,nodes,slots,configs,_,_,calls,injection=fleet
    if fault=='busy':nodes[0]['held_executions']=1
    if fault=='offline':slots[0]['online']=False
    if fault=='protocol':slots[0]['protocol_version']=0
    if fault=='transport':injection['path']='admin/nodes/0/runtime'
    with pytest.raises(RuntimeError):a.prepare()
    if fault=='transport':
        assert a.state['nodes'][nodes[0]['node_name']]['stage']=='preparing'
        recovered=Acceptance(a.api,a.directory,a.run_id);before=len(calls)
        with pytest.raises(RuntimeError,match='reconciliation'):recovered.prepare()
        assert len(calls)==before
    else:assert not any(body is not None for _,body,_ in calls)


@pytest.mark.parametrize('fault',['busy','host','runtime','network','roles'])
def test_cleanup_never_overwrites_operator_changes(fleet,fault):
    a,nodes,_,configs,_,_,calls,_=fleet;a.prepare()
    server=next(n for n in nodes if n['node_name']=='rtx4090-157')
    if fault=='busy':nodes[0]['held_executions']=1
    elif fault=='roles':server['management_revision']+=1
    else:
        key={'host':'host-environments','runtime':'runtime','network':'service-network'}[fault]
        target=server['id'] if fault=='network' else nodes[0]['id']
        configs[target][key]['revision']+=1
    calls.clear()
    with pytest.raises(RuntimeError,match='still has executions|changed; refusing'):a.cleanup()
    assert not any(e['scenario']=='cleanup' for e in a.registry.state['evidence'])
    if fault=='busy':assert not any(p.startswith('admin/nodes/') and b is not None for p,b,_ in calls)
    elif fault=='roles':assert not any(p=='admin/nodes/'+server['id'] and b is not None for p,b,_ in calls)
    else:assert not any(p=='admin/nodes/'+target+'/'+key and b is not None for p,b,_ in calls)


@pytest.mark.parametrize('fault',['none','cleanup','output','same-host','endpoint','gpu'])
def test_execution_evidence_requires_actual_business_and_cleanup(tmp_path,fault):
    echo={'verified_input':True,'endpoint':'http://192.168.8.157:19300','gpu_verified':fault!='gpu'}
    if fault=='endpoint':echo['endpoint']='http://other:19300'
    def api(path):
        if path=='workers':return [{'id':'slot','node_name':'rtx4090-157' if fault=='same-host' else 'client'}]
        if path=='jobs/job':return {'id':'job','attempts':[{'slot_id':'slot','cleanup_confirmed':fault!='cleanup'}]}
        if path=='jobs/job/logs?limit=100':return {'chunks':[{'content':'no output' if fault=='output' else json.dumps(echo)}]}
        raise AssertionError(path)
    a=Acceptance(api,tmp_path,'fixture-run');result={'cases':[{'job_id':'job'}]}
    if fault=='none':assert a.execution_evidence(result,cross_host=True,gpu=True)[0]['echoes']==[echo]
    else:
        with pytest.raises(RuntimeError):a.execution_evidence(result,cross_host=True,gpu=True)


@pytest.mark.parametrize('status',['PENDING','SUCCEEDED','FAILED','TIMED_OUT','CANCELLED'])
def test_completed_does_not_accept_terminal_failure(tmp_path,status):
    result={'status':status,'cases':[{'case_id':'a','status':status,'error':'fixture-error'}]}
    a=Acceptance(lambda _:result,tmp_path,'fixture-run')
    if status in ('FAILED','TIMED_OUT','CANCELLED'):
        with pytest.raises(RuntimeError,match='fixture-error'):a.completed('batch')
    else:assert a.completed('batch')==(result if status=='SUCCEEDED' else None)


def test_matrix_submits_both_backends_access_modes_and_gpu_models(fleet,monkeypatch):
    a,*_=fleet;a.prepare();bodies=[];evidence=[]
    def submit(key,body):bodies.append(body);return key
    monkeypatch.setattr(a,'submit',submit)
    monkeypatch.setattr(a,'completed',lambda identity:{'id':identity})
    monkeypatch.setattr(a,'execution_evidence',lambda result,**flags:evidence.append(flags) or result)
    a.matrix()
    assert len(bodies)==19 and len(evidence)==19
    assert sum(e.get('cross_host',False) for e in evidence)==16
    assert sum(e.get('gpu',False) for e in evidence)==3
    assert {b['services'][0]['mode'] for b in bodies if 'services' in b}=={'shared','exclusive'}
    assert len([e for e in a.registry.state['evidence'] if e['status']=='passed'])==11


@pytest.mark.parametrize('fault',[None,'offline','agent','failed','no-queue','drain-timeout'])
def test_soak_driver_health_queue_and_final_drain(tmp_path,monkeypatch,fault):
    """Virtual time exercises the observer, never claims real two-hour stability."""
    from scripts import smoke_fleet_orchestration as module
    clock={'now':0}
    monkeypatch.setattr(module.time,'monotonic',lambda:clock['now'])
    monkeypatch.setattr(module.time,'sleep',lambda seconds:clock.__setitem__('now',clock['now']+120))
    data={'users':{},'batches':{},'logins':[]}
    class API:
        def __init__(self,base,token):self.base=base.rstrip('/')+'/api/v1/';self.token=token
        def __call__(self,path,body=None,method=None,**kwargs):
            if path=='auth/login':data['logins'].append(body['username']);return {'access_token':body['username']}
            if path=='admin/users':
                if body:data['users'][body['username']]=body;return body
                return list(data['users'].values())
            if path=='admin/nodes':return [{'node_name':name,'held_executions':0,'connection':{'agent':{'status':'offline' if fault=='agent' else 'online'}}} for name in TARGETS.values()]
            if path=='workers':return [{'node_name':name,'online':fault!='offline','quarantined':False} for name in TARGETS.values()]
            if path=='orchestrations':
                identity=str(len(data['batches']));data['batches'][identity]={'body':body,'polls':0};return {'id':identity}
            if path.startswith('orchestrations/'):
                item=data['batches'][path.split('/')[1]];item['polls']+=1
                pending=(item['polls']==1 and fault!='no-queue') or fault=='drain-timeout'
                status='FAILED' if fault=='failed' else 'RUNNING' if pending else 'SUCCEEDED'
                return {'status':status,'cases':[{'status':'PENDING' if pending else status}]}
            raise AssertionError(path)
    monkeypatch.setattr(module,'API',API)
    a=Acceptance(API('http://fixture','admin'),tmp_path,'fixture-run')
    a.state['nodes']={'rtx4090-157':{'pool_id':'pool','slot_id':'server'}}
    a.state['templates']={'client-docker':'client','server-docker':'server'}
    monkeypatch.setattr(a,'execution_evidence',lambda result:[])
    credentials=tmp_path/'admin.json';credentials.write_text(json.dumps({'username':'fixture-admin','password':'fixture-only'}))
    if fault:
        with pytest.raises((RuntimeError,TimeoutError)):a.soak(credentials,endpoint='http://fixture-service')
        assert not any(e['scenario']=='S01' for e in a.registry.state['evidence'])
    else:
        a.soak(credentials,endpoint='http://fixture-service')
        assert len(data['users'])==3 and len(data['batches'])==30
        assert set(data['users'])<=set(data['logins'])
        for b in data['batches'].values():
            assert 'services' not in b['body']
            assert b['body']['client']['service_mode']=='external'
            assert b['body']['client']['endpoint']=='http://fixture-service'
            assert b['body']['gpu_mode']=='shared'
        final=a.registry.state['evidence'][-1]
        assert final['scenario']=='S01' and final['details']['batches']==30 and final['details']['queued_observed']
        progress=json.loads((tmp_path/'soak-progress.json').read_text())
        assert progress['completed']==30


@pytest.mark.parametrize('seconds,batches,endpoint',[(60,30,'http://fixture'),(7200,2,'http://fixture'),(7200,30,None)])
def test_soak_rejects_insufficient_acceptance_window(tmp_path,seconds,batches,endpoint):
    def never(*args,**kwargs):pytest.fail('invalid soak reached API')
    a=Acceptance(never,tmp_path,'fixture-run')
    with pytest.raises(ValueError):a.soak(tmp_path/'absent',seconds,batches,endpoint)
