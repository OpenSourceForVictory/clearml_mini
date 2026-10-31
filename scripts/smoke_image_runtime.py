"""Two-host image runtime acceptance against a disposable API; never use production."""
import argparse
import io
import json
import os
from pathlib import Path
import shlex
import signal
import subprocess
import sys
import tarfile
import time
import urllib.request
import uuid
import threading
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler

ROOT = Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--remote',required=True);p.add_argument('--identity',required=True)
    p.add_argument('--remote-api-port',type=int,default=18440)
    p.add_argument('--output',type=Path,default=ROOT/'.runtime/image-runtime-acceptance.json')
    args=p.parse_args()
    base=os.environ['EVAL_API_URL'].rstrip('/')
    test_host=os.environ['STORAGE_TEST_HOST']
    service=ThreadingHTTPServer((test_host,19091),BaseHTTPRequestHandler)
    threading.Thread(target=service.serve_forever,daemon=True).start()
    if base not in ('http://127.0.0.1:18090','http://localhost:18090'):
        raise ValueError('acceptance requires the isolated local API on port 18090')
    token=None
    def api(path,body=None,method=None):
        req=urllib.request.Request(base+'/api/v1/'+path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method or ('POST' if body is not None else 'GET'),
            headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
        with urllib.request.urlopen(req,timeout=10) as r:return json.load(r)
    token=api('auth/login',{'username':os.environ['ADMIN_USERNAME'],'password':os.environ['ADMIN_PASSWORD']})['access_token']
    suffix=uuid.uuid4().hex[:10]
    ssh=['ssh','-o','BatchMode=yes','-o','IdentitiesOnly=yes','-o','ConnectTimeout=10','-i',args.identity,args.remote]
    def remote(argv,**kwargs):
        return subprocess.run(ssh+[shlex.join(argv)],check=True,capture_output=True,**kwargs)
    def wait(fn,timeout=120):
        deadline=time.monotonic()+timeout
        while time.monotonic()<deadline:
            value=fn()
            if value:return value
            time.sleep(.5)
        raise TimeoutError('image runtime acceptance condition timed out')
    local=ROOT/'.runtime'/('image-runtime-'+suffix);local.mkdir(parents=True)
    remote_root=Path(remote(['python3','-c',f"from pathlib import Path; p=Path.home()/'mini-eval-image-runtime-{suffix}'; p.mkdir(); print(p)"],text=True).stdout.strip())
    archive=io.BytesIO()
    with tarfile.open(fileobj=archive,mode='w') as tar:
        for directory in ('worker','runner','examples/image-client'):
            for f in (ROOT/directory).rglob('*'):
                if f.is_file() and '__pycache__' not in f.parts:tar.add(f,arcname=str(f.relative_to(ROOT)))
    remote(['tar','xf','-','-C',str(remote_root)],input=archive.getvalue())
    build=remote(['docker','build','-t','mini-eval-image-check:1','-f',str(remote_root/'examples/image-client/Dockerfile'),str(remote_root)],text=True)
    (local/'remote-build.log').write_text(build.stdout+build.stderr)
    tunnel=subprocess.Popen(ssh[:-1]+['-o','ExitOnForwardFailure=yes','-N','-R',f'127.0.0.1:{args.remote_api_port}:127.0.0.1:18090',args.remote],stdout=subprocess.DEVNULL,stderr=(local/'tunnel.log').open('w'))
    workers=[];process=None;remote_pid=None;evidence={'kind':'image-runtime-contract-not-model-evaluation','hosts':{},'batches':[]}
    pool=api('admin/pools',{'name':'image-runtime-'+suffix,'gpu_model':'virtual'})['id']
    template=api('admin/client-templates',{'name':'image-runtime-'+suffix,'version':'1','image':'mini-eval-image-check:1',
        'command':['python3','/app/image_check.py'],'episode_reporting':True,
        'runtime':{'environment':{'TASK':'${task_name}','ASSETS':'${assets:robotwin}'},'overridable_env':['LOG_LEVEL'],
                   'work_dir':'/workspace/eval','output_dir':'/results','assets':{'robotwin':'/data/robotwin'}}})['id']
    def start_local():
        return subprocess.Popen([sys.executable,'-m','worker.client','--config',str(local/'worker.json')],cwd=ROOT,
            stdout=(local/'worker.log').open('a'),stderr=subprocess.STDOUT)
    def storage(w):
        result=api('admin/nodes/'+w['id']+'/runtime')
        result['all_applied']=bool(result['slots']) and all(x['ready'] and x['report']['images'].get('mini-eval-image-check:1',{}).get('ready') and x['report']['assets'].get('robotwin',{}).get('ready') for x in result['slots'])
        return result
    def save(w,config):
        current=storage(w)
        return api('admin/nodes/'+w['id']+'/runtime',{'expected_revision':current['revision'],'config':config},'PUT')
    def batch(label,sleep=1):
        b=api('batches',{'title':'image-runtime-'+suffix+'-'+label,'pool_id':pool,'max_concurrent':2,
            'cases':[{'case_id':str(i),'task_name':'mount-check','episodes':2,'seed':i} for i in range(2)],
            'client':{'template_id':template,'endpoint':f'http://{test_host}:19091','environment':{'LOG_LEVEL':'acceptance'},'parameters':{'sleep_seconds':sleep},'timeout_seconds':120}})
        evidence['batches'].append(b['id']);return [x['job_id'] for x in b['cases']]
    def jobs(ids):return [api('jobs/'+j) for j in ids]
    def running(ids):
        values=jobs(ids);return values if all(j['status']=='RUNNING' for j in values) else None
    def done(ids):
        values=jobs(ids)
        if all(j['status'] in ('SUCCEEDED','FAILED','TIMED_OUT','CANCELLED') for j in values):
            assert all(j['status']=='SUCCEEDED' for j in values),values
            return values
    def mounts(values, marker):
        result=[]
        for j in values:
            run=j['attempts'][-1]
            assert len(j['attempts']) == 1
            prefix=f"jobs/{j['id']}/attempts/{run['id']}"
            progress=api(prefix+'/episodes')['progress']
            assert progress['completed']==progress['successes']==2
            artifacts=[api(prefix+'/artifacts/'+a['id']) for a in api(prefix+'/artifacts') if a['name'].startswith('mounts-')]
            assert len(artifacts)==2 and all(a['marker'].startswith(marker) for a in artifacts)
            result.append({'job_id':j['id'],'run_id':run['id'],'slot_id':run['slot_id'],
                'status':j['status'],'metrics':run['metrics'],'progress':progress,'artifacts':artifacts})
        assert len({r['slot_id'] for r in result})==2
        return result
    def remote_start():
        code="import subprocess; from pathlib import Path; root=Path("+repr(str(remote_root))+"); log=open(root/'worker.log','a'); p=subprocess.Popen(['python3','-m','worker.client','--config',str(root/'worker.json')],cwd=root,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True); print(p.pid)"
        return int(remote(['python3','-c',code],text=True).stdout.strip())
    try:
        for label,root,url in [('local',local,base),('r8000',remote_root,f'http://127.0.0.1:{args.remote_api_port}')]:
            w=api('admin/worker-credentials',{'node_name':'storage-'+label+'-'+suffix})
            workers.append(w)
            config={'api_url':url,'token':w['token'],'node_name':w['node_name'],'pool_id':pool,'mock':True,
                'runner_image':'mini-eval-runner:robotwin-render','client_images':['mini-eval-image-check:1'],
                'asset_mappings':{'robotwin':str(root/'original/assets')},'workspace_root':str(root/'original'),'state_path':str(root/'state.json'),'min_free_disk_gb':0}
            setup="""import json,sys,os
from pathlib import Path
cfg=json.load(sys.stdin);root=Path(cfg['workspace_root']).parent
for name in ('original/assets','original/cache','new-assets','new-cache'):
    target=root/name;target.mkdir(parents=True,exist_ok=True);target.chmod(0o755)
    (target/'marker.txt').write_text(('old-' if name.startswith('original') else 'new-')+cfg['node_name'])
p=root/'worker.json';p.write_text(json.dumps(cfg));p.chmod(0o600)
"""
            if label=='local':subprocess.run([sys.executable,'-c',setup],input=json.dumps(config),text=True,check=True)
            else:remote(['python3','-c',setup],input=json.dumps(config),text=True)
            evidence['hosts'][label]={'worker_id':w['id'],'root':str(root)}
        process=start_local();remote_pid=remote_start()
        wait(lambda:all(storage(w)['all_applied'] for w in workers))
        for w,root in zip(workers,(local,remote_root)):
            save(w,{'images':['mini-eval-image-check:1'],'assets':{'robotwin':str(root/'missing-assets')}})
        wait(lambda:all(storage(w)['slots'][0]['report']['applied_revision']==1 and storage(w)['slots'][0]['report']['assets'].get('robotwin',{}).get('ready') is False for w in workers))
        pending=batch('missing-assets')
        time.sleep(3)
        assert all(j['status']=='PENDING' for j in jobs(pending))
        evidence['missing_asset_readiness']=[api('jobs/'+j+'/runtime-readiness') for j in pending]
        for w in workers:save(w,None)
        wait(lambda:all(storage(w)['all_applied'] for w in workers))
        evidence['resumed_jobs']=mounts(wait(lambda:done(pending)),'old-')
        initial=batch('old-path',sleep=10)
        evidence['original_run_ids']=[j['attempts'][-1]['id'] for j in wait(lambda:running(initial))]
        for w,root in zip(workers,(local,remote_root)):
            save(w,{'images':['mini-eval-image-check:1'],'assets':{'robotwin':str(root/'new-assets')}})
        wait(lambda:all(storage(w)['slots'][0]['report']['revision']==3 and storage(w)['slots'][0]['report']['applied_revision']==2 for w in workers))
        evidence['waiting']=[storage(w) for w in workers]
        # Kill only the dedicated local test worker, leaving its container and journal for recovery.
        process.kill();process.wait(timeout=10);process=start_local()
        evidence['old_jobs']=mounts(wait(lambda:done(initial)), 'old-')
        assert evidence['original_run_ids']==[r['run_id'] for r in evidence['old_jobs']]
        wait(lambda:all(storage(w)['all_applied'] and storage(w)['revision']==3 for w in workers))
        evidence['managed']=[storage(w) for w in workers]
        new=batch('new-path',sleep=4);wait(lambda:running(new))
        evidence['local_mounts']=json.loads(subprocess.check_output(['docker','inspect',*subprocess.check_output(['docker','ps','-q','--filter','label=mini-eval.slot='+storage(workers[0])['slots'][0]['slot_id']],text=True).split()],text=True))
        ids=remote(['docker','ps','-q','--filter','label=mini-eval.slot='+storage(workers[1])['slots'][0]['slot_id']],text=True).stdout.split()
        evidence['remote_mounts']=json.loads(remote(['docker','inspect',*ids],text=True).stdout)
        evidence['new_jobs']=mounts(wait(lambda:done(new)), 'new-')
        for w in workers:save(w,None)
        wait(lambda:all(storage(w)['all_applied'] and storage(w)['revision']==4 for w in workers))
        evidence['restored']=[storage(w) for w in workers]
        final=batch('restored',sleep=6);wait(lambda:running(final))
        evidence['restored_jobs']=mounts(wait(lambda:done(final)), 'old-')
        for key,report in zip(('local_mounts','remote_mounts'),evidence['managed']):
            effective=report['slots'][0]['report']['effective']
            for value in evidence[key]:
                bindings={m['Destination']:m for m in value['Mounts']}
                assert bindings['/data/robotwin']['Source']==effective['assets']['robotwin'] and not bindings['/data/robotwin']['RW']
                assert bindings['/workspace/eval']['Source']==bindings['/tmp/mini-eval-work']['Source']
                assert bindings['/results']['Source']==bindings['/tmp/mini-eval-output']['Source']
                assert bindings['/workspace/eval']['RW'] and bindings['/results']['RW']
            evidence[key]=[{'name':v['Name'],'mounts':v['Mounts'],'execution_id':v['Config']['Labels']['mini-eval.execution']} for v in evidence[key]]
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(evidence,indent=2))
        print(json.dumps({'result':'passed','jobs':8,'episodes':16,'evidence':str(args.output)},ensure_ascii=False))
    finally:
        if process and process.poll() is None:process.terminate();process.wait(timeout=30)
        if remote_pid:
            remote(['python3','-c',f'import os,signal; os.kill({remote_pid},signal.SIGTERM)'])
        for w in workers:api('admin/nodes/'+w['id'],{'enabled':False},'PATCH')
        tunnel.terminate();tunnel.wait(timeout=10)
        service.shutdown();service.server_close()
        print('Test logs:',local,remote_root)


if __name__=='__main__':main()
