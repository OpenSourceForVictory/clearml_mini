"""Local Docker/host protocol acceptance against a disposable API on port 18090."""
import json,os,subprocess,sys,time,urllib.request
from pathlib import Path


def main():
    base=os.environ.get('EVAL_API_URL','http://127.0.0.1:18090')
    if base!='http://127.0.0.1:18090':raise ValueError('requires isolated API on 127.0.0.1:18090')
    root=(Path('.runtime/host-runtime-smoke')/str(time.time_ns())).resolve();root.mkdir(parents=True,exist_ok=True)
    token=None
    def api(path,body=None,method=None):
        req=urllib.request.Request(base+'/api/v1/'+path,data=None if body is None else json.dumps(body).encode(),method=method or ('POST' if body is not None else 'GET'),headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
        with urllib.request.urlopen(req,timeout=10) as r:return json.load(r)
    def wait(fn,seconds=90):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if (value:=fn()):return value
            time.sleep(.5)
        raise RuntimeError('acceptance timed out')
    token=api('auth/login',{'username':'admin','password':os.environ['EVAL_ADMIN_PASSWORD']})['access_token']
    pool=api('admin/pools',{'name':'host-smoke-'+str(time.time_ns()),'gpu_model':'virtual'})
    node=api('admin/worker-credentials',{'node_name':'host-smoke-'+str(time.time_ns())})
    for directory in ('work','assets','cache'):(root/directory).mkdir(exist_ok=True)
    cfg={'api_url':base,'token':node['token'],'node_name':node['node_name'],'pool_id':pool['id'],'mock':True,'gpu_index':0,'runner_image':'mini-eval-runner:dev','client_images':['mini-eval-runner:dev'],'workspace_root':str(root/'work'),'assets_root':str(root/'assets'),'cache_root':str(root/'cache'),'state_path':str(root/'state.json'),'min_free_disk_gb':0,'execution_modes':['docker','host']}
    config=root/'worker.json';config.write_text(json.dumps(cfg));config.chmod(0o600)
    api('admin/nodes/'+node['id']+'/host-environments',{'expected_revision':0,'environments':{'test':{'python':'/usr/bin/python3','code_root':str(Path.cwd())}}},'PUT')
    handles=[];processes=[];results=[]
    try:
        for name,cmd in [('inference',[sys.executable,'-m','scripts.cs_test_service','--host',os.environ['STORAGE_TEST_HOST'],'--port','19092']),('worker',[sys.executable,'-m','worker.client','--config',str(config)])]:
            f=(root/(name+'.log')).open('w');handles.append(f);processes.append(subprocess.Popen(cmd,stdout=f,stderr=f))
        def ready():
            slots=api('workers');return any(x['node_name']==node['node_name'] and x['online'] for x in slots) and any(x.get('report',{}).get('environments',{}).get('test',{}).get('ready') for x in api('admin/nodes/'+node['id']+'/host-environments')['slots'] if x.get('report'))
        wait(ready)
        for mode in ['docker','host']:
            for integration in ['sdk-command','direct']:
                command=['python3','-c',"import os,json;open(os.environ['MINI_EVAL_RESULT'],'w').write(json.dumps({'success':True,'metrics':{'protocol_example':1}}))"] if integration=='sdk-command' else ['python3','-m','runner.sdk','runner.sdk_demo:DemoClient']
                body={'name':node['node_name']+'-'+mode+'-'+integration,'version':'1','execution_mode':mode,'integration_mode':integration,'episode_reporting':True,'command':command,**({'image':'mini-eval-runner:dev','runtime':{}} if mode=='docker' else {'host_runtime':{'environment_name':'test'}})}
                template=api('admin/client-templates',body)
                job=api('jobs',{'title':mode+' '+integration,'pool_id':pool['id'],'benchmark':'cs-client','episodes':2,'client':{'template_id':template['id'],'endpoint':'http://'+os.environ['STORAGE_TEST_HOST']+':19092/infer','task_name':'echo','timeout_seconds':60}})
                def done():
                    v=api('jobs/'+job['id']);return v if v['status'] in ['SUCCEEDED','FAILED','TIMED_OUT','CANCELLED'] else None
                value=wait(done)
                assert value['status']=='SUCCEEDED',(mode,integration,value.get('error'),value['attempts'])
                attempt=value['attempts'][-1];episodes=api('jobs/'+job['id']+'/attempts/'+attempt['id']+'/episodes')
                assert episodes['progress']['completed']==2
                artifacts=api('jobs/'+job['id']+'/attempts/'+attempt['id']+'/artifacts')
                assert any(x['name']=='report.json' for x in artifacts), artifacts
                assert api('jobs/'+job['id']+'/logs')['chunks']
                results.append({'mode':mode,'integration':integration,'job_id':job['id'],'completed':2})
                print('PASS',mode,integration,flush=True)
        (root/'evidence.json').write_text(json.dumps(results,indent=2))
    finally:
        for p in processes:p.terminate()
        for p in processes:
            try:p.wait(timeout=30)
            except subprocess.TimeoutExpired:p.kill();p.wait()
        for f in handles:f.close()
if __name__=='__main__':main()
