"""Disposable two-slot service/client acceptance. Never targets production API."""
import json,os,subprocess,sys,time,urllib.request
from pathlib import Path


def main():
    base=os.environ.get('EVAL_API_URL','http://127.0.0.1:18090')
    if base!='http://127.0.0.1:18090':raise ValueError('requires isolated API')
    root=(Path('.runtime/service-smoke')/str(time.time_ns())).resolve();root.mkdir(parents=True)
    token=None
    def api(path,body=None,method=None):
        req=urllib.request.Request(base+'/api/v1/'+path,data=None if body is None else json.dumps(body).encode(),method=method or ('POST' if body is not None else 'GET'),headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
        with urllib.request.urlopen(req,timeout=10) as r:return json.load(r)
    def wait(fn,seconds=90):
        end=time.monotonic()+seconds
        while time.monotonic()<end:
            if (v:=fn()):return v
            time.sleep(.5)
        raise RuntimeError('service acceptance timed out')
    token=api('auth/login',{'username':'admin','password':os.environ['EVAL_ADMIN_PASSWORD']})['access_token']
    pool=api('admin/pools',{'name':'service-smoke-'+str(time.time_ns()),'gpu_model':'virtual'})
    n=api('admin/worker-credentials',{'node_name':'service-smoke-'+str(time.time_ns()),'roles':['client','server'],'max_concurrent':2})
    api('admin/nodes/'+n['id']+'/host-environments',{'expected_revision':0,'environments':{'test':{'python':'/usr/bin/python3','code_root':str(Path.cwd())}}},'PUT')
    processes=[];handles=[];instances=[];results=[]
    try:
        for i in range(2):
            for d in ('work','assets','cache'):(root/str(i)/d).mkdir(parents=True)
            config={'api_url':base,'token':n['token'],'node_name':n['node_name'],'pool_id':pool['id'],'mock':True,'gpu_index':i,'runner_image':'mini-eval-runner:dev','client_images':['mini-eval-runner:dev'],'state_path':str(root/str(i)/'state.json'),'workspace_root':str(root/str(i)/'work'),'assets_root':str(root/str(i)/'assets'),'cache_root':str(root/str(i)/'cache'),'min_free_disk_gb':0}
            path=root/str(i)/'worker.json';path.write_text(json.dumps(config));path.chmod(0o600)
            f=(root/str(i)/'worker.log').open('w');handles.append(f);processes.append(subprocess.Popen([sys.executable,'-m','worker.client','--config',str(path)],stdout=f,stderr=f))
        slots=wait(lambda:(v if len(v:=[s for s in api('workers') if s['worker_id']==n['id']])==2 else None));target=next(s for s in slots if s['gpu_index']==0)
        client=api('admin/client-templates',{'name':n['node_name']+'-client','version':'1','image':'mini-eval-runner:dev','runtime':{},'command':['python3','-m','runner.sdk','runner.sdk_demo:DemoClient'],'episode_reporting':True})
        def current(identity):return next(i for i in api('admin/services') if i['id']==identity)
        for mode in ['docker','host']:
            t=api('admin/server-templates',{'name':n['node_name']+'-'+mode,'version':'1','parameter_schema':{'version':1,'fields':{'sample_label':{'type':'string','default':'service-smoke','binding':{'target':'env','name':'SERVICE_SAMPLE_LABEL'}}}},'execution_mode':mode,'command':['python3','-m','runner.inference_demo','--host','${bind_host}','--port','${port}'],**({'image':'mini-eval-runner:dev'} if mode=='docker' else {'host_runtime':{'environment_name':'test'}})})
            instance=api('admin/services',{'name':mode+' service','template_id':t['id'],'slot_id':target['id'],'address':os.environ['STORAGE_TEST_HOST'],'port':19093});instances.append(instance['id'])
            def healthy():
                v=current(instance['id'])
                if v['state'] in ['FAILED','TIMED_OUT']:raise RuntimeError(str(v))
                return v if v['state']=='HEALTHY' else None
            wait(healthy)
            job=api('jobs',{'title':'client to '+mode,'pool_id':pool['id'],'benchmark':'cs-client','episodes':2,'client':{'template_id':client['id'],'endpoint':instance['endpoint']+'/infer','task_name':'echo','timeout_seconds':60}})
            value=wait(lambda:(v if (v:=api('jobs/'+job['id']))['status'] in ['SUCCEEDED','FAILED','TIMED_OUT'] else None))
            assert value['status']=='SUCCEEDED',value
            logs=api('admin/services/'+instance['id']+'/logs');assert 'starting-service' in logs['content'],logs
            v=current(instance['id']);api('admin/services/'+v['id']+'/control',{'action':'stop','expected_revision':v['revision']})
            wait(lambda:current(v['id'])['state']=='STOPPED')
            stopped=current(v['id']);restarted=api('admin/services/'+v['id']+'/control',{'action':'start','expected_revision':stopped['revision']})
            assert restarted['current_job_id']!=instance['current_job_id'];wait(healthy)
            v=current(v['id']);api('admin/services/'+v['id']+'/control',{'action':'stop','expected_revision':v['revision']});wait(lambda:current(v['id'])['state']=='STOPPED')
            assert len(api('admin/services/'+v['id']+'/logs')['history'])==2
            results.append({'mode':mode,'instance_id':v['id'],'client_job':job['id'],'restart_verified':True});print('PASS service',mode,'client, health, logs, stop and restart',flush=True)
        (root/'evidence.json').write_text(json.dumps(results,indent=2))
    finally:
        for identity in instances:
            try:
                v=next(i for i in api('admin/services') if i['id']==identity);api('admin/services/'+identity+'/control',{'action':'stop','expected_revision':v['revision']})
            except Exception:pass
        for p in processes:p.terminate()
        for p in processes:
            try:p.wait(timeout=30)
            except subprocess.TimeoutExpired:p.kill();p.wait()
        for f in handles:f.close()
if __name__=='__main__':main()
