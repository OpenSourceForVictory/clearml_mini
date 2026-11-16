"""Explicit five-physical-node acceptance using real Workers and small HTTP echo services."""
import argparse
import copy
import json
from pathlib import Path
import time
from scripts.fleet_release import atomic_json, TARGETS
from scripts.fleet_release_api import API
from scripts.fleet_acceptance import Registry
from scripts.fleet_probe_programs import SERVER, CLIENT, COMMAND

IMAGE = 'mini-eval-runner:dev'


class Acceptance:
    def __init__(self, api, directory, run_id):
        self.api, self.directory, self.run_id = api, Path(directory), run_id
        self.registry = Registry(self.directory/'registry.json', run_id)
        self.path = self.directory/'setup.json'
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {'run_id': run_id, 'nodes': {}, 'templates': {}, 'batches': {}}
        if self.state['run_id'] != run_id: raise ValueError('acceptance run changed')

    def save(self): atomic_json(self.path, self.state)

    def prepare(self):
        nodes = self.api('admin/nodes')
        slots = self.api('workers')
        for address, name in TARGETS.items():
            node = next(n for n in nodes if n['node_name'] == name and not n.get('revoked_at'))
            if node['held_executions']: raise RuntimeError('prepare requires idle target nodes')
            slot = next(s for s in slots if s['worker_id'] == node['id'])
            if not slot['online'] or slot['protocol_version'] != 10: raise RuntimeError('upgrade fleet first')
            route = 'admin/nodes/' + node['id']
            if name in self.state['nodes']: continue
            host = self.api(route+'/host-environments'); runtime = self.api(route+'/runtime')
            network = self.api(route+'/service-network')
            saved = {'id': node['id'], 'slot_id': slot['id'], 'pool_id': slot['pool_id'],
                     'address': address, 'roles': node['roles'], 'host': host,
                     'runtime': runtime, 'network': network, 'stage': 'preparing'}
            self.state['nodes'][name] = saved; self.save()
            if address == '192.168.8.157' and 'server' not in node['roles']:
                changed = self.api(route, {'roles': sorted(set(node['roles']+['server'])),
                                          'expected_revision': node['management_revision']}, 'PATCH')
                saved['roles_revision'] = changed['management_revision']; self.save()
            profile = 'fleet_acceptance'
            home = '/home/dgh' if address == '192.168.8.151' else '/home/huawei'
            environments = {**host['environments'], profile: {'python': '/usr/bin/python3', 'code_root': home}}
            saved['host_revision'] = self.api(route+'/host-environments', {'expected_revision':host['revision'], 'environments':environments}, 'PUT')['revision']; self.save()
            effective = copy.deepcopy(runtime['config'] or (runtime['slots'][0].get('report') or {}).get('effective') or {'images': [], 'assets': {}})
            effective['images'] = sorted(set(effective.get('images', []) + [IMAGE]))
            saved['runtime_revision'] = self.api(route+'/runtime', {'expected_revision':runtime['revision'], 'config':effective}, 'PUT')['revision']; self.save()
            if address == '192.168.8.157':
                saved['network_revision'] = self.api(route+'/service-network', {'expected_revision':network['revision'], 'config':{'address': address, 'port_start':19300, 'port_end':19320}}, 'PUT')['revision']; self.save()
            saved['stage'] = 'ready'; self.save()
        if any(n['stage'] != 'ready' for n in self.state['nodes'].values()):
            raise RuntimeError('interrupted setup requires reconciliation before running')
        self.wait(lambda: all(self.api('admin/nodes/'+n['id']+'/runtime')['slots'][0]['ready'] and
                             (self.api('admin/nodes/'+n['id']+'/host-environments')['slots'][0].get('report') or {}).get('applied_revision') == n['host_revision']
                             for n in self.state['nodes'].values()), 120)
        for mode in ('host','docker'):
            for kind in ('server','client','command'):
                key = kind+'-'+mode
                if key in self.state['templates']: continue
                name = self.run_id+'-'+key
                route = 'admin/server-templates' if kind == 'server' else 'client-templates'
                matches = [t for t in self.api(route) if t['name'] == name and t['version'] == '1']
                if matches:
                    result = matches[0]
                else:
                    body = {'name': name, 'version':'1','execution_mode':mode,
                            'command':['python3','-c',{'server':SERVER,'client':CLIENT,'command':COMMAND}[kind]]}
                    if mode == 'host': body['host_runtime'] = {'environment_name':'fleet_acceptance'}
                    else: body.update(image=IMAGE,runtime={})
                    if kind == 'command': body['task_type'] = 'command'
                    elif kind == 'client': body.update(integration_mode='sdk-command',episode_reporting=True)
                    else: body.update(startup_timeout=30)
                    result = self.api('admin/'+('server' if kind == 'server' else 'client')+'-templates',body,'POST')
                actual_kind = 'server' if kind == 'server' else 'client'
                self.registry.register(actual_kind+'-template',result['id'],'admin',name)
                self.state['templates'][key] = result['id']; self.save()
                if kind == 'server' and not result.get('available_to_members'):
                    self.api('admin/server-templates/'+result['id']+'/access',{'expected_revision':result.get('revision',0),'available_to_members':True},'PATCH')

    @staticmethod
    def wait(check, timeout):
        until = time.monotonic()+timeout
        while time.monotonic()<until:
            result=check()
            if result:return result
            time.sleep(2)
        raise TimeoutError('acceptance deadline exceeded')

    def body(self, client_mode='docker', server_mode='docker', access='shared', command=False, model=None, sleep_seconds=1):
        server = self.state['nodes']['rtx4090-157']
        result={'title':'', 'pool_id':server['pool_id'], 'max_concurrent':3,
                'client':{'template_id':self.state['templates'][('command' if command else 'client')+'-'+client_mode],
                          'service_mode':'none' if command else 'managed'},
                'cases':[{'case_id':str(i),'task_name':'echo-'+str(i),'episodes':1 if command else 2,
                          'parameters':{'variant':i,'sleep_seconds':sleep_seconds}} for i in range(3)]}
        if model:result['required_gpu_model']=model
        if not command:
            result['services']=[{'template_id':self.state['templates']['server-'+server_mode], 'pool_id':server['pool_id'],
                                 'slot_id':server['slot_id'], 'mode':access,'max_instances':1,'parameters':{'model':'fleet-test-model'}}]
        return result

    def submit(self, key, body, api=None, owner='admin'):
        api = api or self.api
        title = self.run_id+'-'+key
        body={**body,'title':title}
        previous=self.state['batches'].get(key)
        if previous:
            if previous['body']!=body or previous['owner']!=owner:raise ValueError('submission changed')
        else:
            self.state['batches'][key]={'body':body,'owner':owner};self.save()
        result=api('orchestrations',body,'POST',idempotency_key=title)
        self.registry.register('batch',result['id'],owner,title)
        self.state['batches'][key]['id']=result['id'];self.save()
        return result['id']

    def completed(self, identity, api=None):
        api=api or self.api
        result=api('orchestrations/'+identity)
        if result['status'] in ('FAILED','TIMED_OUT','CANCELLED'):
            raise RuntimeError('test batch failed: '+identity+' '+json.dumps([(c['case_id'],c['status'],c.get('error')) for c in result['cases']]))
        return result if result['status']=='SUCCEEDED' else None

    def matrix(self):
        for client in ('host','docker'):
            for server in ('host','docker'):
                for access in ('shared','exclusive'):
                    key=client+'-'+server+'-'+access
                    batches=[self.submit(key+'-'+str(i),self.body(client,server,access,model='Quadro RTX 8000')) for i in range(2)]
                    results=[self.wait(lambda b=b:self.completed(b),240) for b in batches]
                    self.wait(lambda: all(n['held_executions']==0 for n in self.api('admin/nodes') if n['node_name'] in TARGETS.values()),90)
                    evidence = [self.execution_evidence(result, cross_host=True) for result in results]
                    self.registry.record('E02-'+key,'passed',{'batches':batches,'results':evidence})
                    print('PASS',key,flush=True)
        for model in ('NVIDIA GeForce RTX 4090','NVIDIA GeForce RTX 3080','Quadro RTX 8000'):
            key='gpu-'+str(len(self.state['batches']))
            batch=self.submit(key,self.body(command=True,model=model,sleep_seconds=10))
            result=self.wait(lambda:self.completed(batch),180)
            self.registry.record('E01-'+model,'passed',{'batch':batch,'result':self.execution_evidence(result, gpu=True)})
            print('PASS GPU',model,flush=True)

    def execution_evidence(self, result, cross_host=False, gpu=False):
        slots = {s['id']:s for s in self.api('workers')}
        entries=[]
        for case in result['cases']:
            job=self.api('jobs/'+case['job_id'])
            attempt=job['attempts'][-1]
            if not attempt['cleanup_confirmed']:raise RuntimeError('client cleanup is not confirmed')
            logs=self.api('jobs/'+case['job_id']+'/logs?limit=100')
            content=''.join(c['content'] for c in logs['chunks'])
            echoes=[json.loads(line) for line in content.splitlines() if line.startswith('{"verified_input"') or line.startswith('{"gpu_verified"')]
            if not echoes:raise RuntimeError('missing actual business verification output')
            if cross_host:
                if slots[attempt['slot_id']]['node_name']=='rtx4090-157':raise RuntimeError('client/server share a physical host')
                if any(not e['endpoint'].startswith('http://192.168.8.157:') for e in echoes):raise RuntimeError('unexpected service address')
            if gpu and any(e.get('gpu_verified') is not True for e in echoes):raise RuntimeError('missing real CUDA validation')
            entries.append({'job_id':job['id'],'node':slots[attempt['slot_id']]['node_name'],'attempt':attempt,'echoes':echoes})
        return entries


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['prepare','matrix'])
    p.add_argument('--base',default='http://127.0.0.1:18000')
    p.add_argument('--token-file',type=Path,required=True)
    p.add_argument('--directory',type=Path,required=True)
    p.add_argument('--run-id',required=True)
    args=p.parse_args()
    api=API(args.base,json.loads(args.token_file.read_text())['token'])
    acceptance=Acceptance(api,args.directory,args.run_id)
    getattr(acceptance,args.action)()

if __name__=='__main__':main()
