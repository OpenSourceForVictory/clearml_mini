"""Real batch controls and retries, confined to registered fleet acceptance batches."""
import argparse,json,secrets,time
from pathlib import Path
from scripts.fleet_release_api import API
from scripts.smoke_fleet_orchestration import Acceptance

RETRY='''import json,os
from pathlib import Path
c=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
p=Path(os.environ['MINI_EVAL_CACHE_DIR'])/('fleet-retry-'+c['business']['marker']+'-'+c['task']['case_id'])
try:
 fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);os.close(fd)
except FileExistsError:
 print(json.dumps({'retried_successfully':True,'execution_id':c['platform']['execution_id']}),flush=True)
else:raise SystemExit(23)
'''


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--token-file',type=Path,required=True);p.add_argument('--directory',type=Path,required=True);p.add_argument('--run-id',required=True)
    a=p.parse_args();api=API('http://127.0.0.1:18000',json.loads(a.token_file.read_text())['token'])
    test=Acceptance(api,a.directory,a.run_id)
    key='controls-'+str(time.time_ns())
    body=test.body(command=True,model='NVIDIA GeForce RTX 3080',sleep_seconds=15);body['max_concurrent']=1
    identity=test.submit(key,body)
    def get():return api('batches/'+identity+'/monitor')
    initial=get();api('batches/'+identity+'/control',{'action':'pause','expected_revision':initial['control_revision']},'POST')
    paused=get();assert paused['scheduling_paused']
    reserved=[c for c in paused['cases'] if c['status']=='RESERVED'];assert not reserved
    time.sleep(3);assert get()['scheduling_paused']
    api('batches/'+identity+'/control',{'action':'resume','expected_revision':paused['control_revision']},'POST')
    running=test.wait(lambda:next((c for c in get()['cases'] if c['status']=='RUNNING'),None),120)
    test.registry.cancel_batch(api,identity,'admin')
    test.wait(lambda:all(c['status'] in ('SUCCEEDED','CANCELLED') for c in get()['cases']),120)
    def clean():return all(api('jobs/'+c['job_id'])['attempts'][-1]['cleanup_confirmed'] for c in get()['cases'])
    test.wait(clean,60)
    retried=api('jobs/'+running['job_id']+'/retry',{},'POST');assert retried['attempt']==2
    current=get();api('batches/'+identity+'/control',{'action':'resume','expected_revision':current['control_revision']},'POST')
    job=test.wait(lambda:(j if (j:=api('jobs/'+running['job_id']))['status']=='SUCCEEDED' else None),120)
    assert len(job['attempts'])==2
    test.registry.record('E06-controls','passed',{'batch':identity,'retried_job':job['id'],'attempts':job['attempts']})
    name=test.run_id+'-failure-retry-host'
    templates=api('client-templates');existing=next((t for t in templates if t['name']==name),None)
    template=existing or api('admin/client-templates',{'name':name,'version':'1','execution_mode':'host','host_runtime':{'environment_name':'fleet_acceptance'},'task_type':'command','command':['python3','-c',RETRY]},'POST')
    test.registry.register('client-template',template['id'],'admin',name)
    marker=secrets.token_hex(12)
    body=test.body(command=True,model='NVIDIA GeForce RTX 3080');body['client']['template_id']=template['id']
    for case in body['cases']:case['parameters']={'marker':marker}
    identity=test.submit('bulk-'+marker,body)
    test.wait(lambda:all(c['status']=='FAILED' for c in api('batches/'+identity+'/monitor')['cases']),180)
    current=api('batches/'+identity+'/monitor')
    test.wait(lambda:all(api('jobs/'+c['job_id'])['attempts'][-1]['cleanup_confirmed'] for c in current['cases']),60)
    result=api('batches/'+identity+'/retry-failed',{'items':[{'job_id':c['job_id'],'expected_run_id':c['run_id']} for c in current['cases']]},'POST')
    assert result['retried']==3 and result['skipped']==0
    completed=test.wait(lambda:test.completed(identity),180)
    test.registry.record('E06-bulk-retry','passed',{'batch':identity,'retried':result,'completed':completed})
    for case in body['cases']:
        marker_path=Path('/mnt/eval-share/cache')/('fleet-retry-'+marker+'-'+case['case_id'])
        if marker_path.is_symlink():raise RuntimeError('unexpected retry marker symlink')
        marker_path.unlink(missing_ok=True)
    test.registry.disable_template(api,'client',template['id'],'admin')
    print('PASS pause/resume/cancel/single retry and three-item failed bulk retry',flush=True)

if __name__=='__main__':main()
