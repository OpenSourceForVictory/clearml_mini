"""One source for importable examples and real execution acceptance tests."""
import json
from pathlib import Path


def template(kind,mode,image='mini-eval-scheduling-acceptance:v1',version='v1'):
    fields={'label':{'type':'string','default':kind+'-label','overridable':True,'binding':{'target':'env','name':'DEMO_LABEL'}}}
    if kind=='client':
        fields.update(variant={'type':'integer','min':0,'max':2,'default':0,'overridable':True,'binding':{'target':'argv','name':'--variant'}},sleep_seconds={'type':'number','min':0,'max':60,'default':1,'overridable':True},fail={'type':'boolean','default':False,'overridable':True})
    else:fields['model']={'type':'string','default':'test-model','overridable':True,'binding':{'target':'argv','name':'--model'}}
    env={'DEMO_ASSETS':'${assets:acceptance_data}' if mode=='host' else '${mounts:dataset}',
         'VERIFY_LIMITS':'1','VERIFY_READ_ONLY':'0' if mode=='host' else '1'}
    runtime={'host_runtime':{'environment_name':'acceptance_demo','environment':env,'assets':['acceptance_data']}} if mode=='host' else {'image':image,'runtime':{'environment':env,'work_dir':'/sample-work','output_dir':'/sample-output','mounts':{'dataset':{'source':'assets:acceptance_data','target':'/sample-assets','read_only':True,'required':True}}}}
    value={**runtime,'name':f'acceptance-{kind}-{mode}','version':version,'execution_mode':mode,
           'command':['python3',('' if mode=='host' else '/demo/')+kind+'.py'],
           'parameter_schema':{'version':1,'fields':fields},
           'resource_requirements':{'version':1,'cpu':{'minimum':1,'default':1},'memory':{'minimum':1,'default':1},'accelerator':{'type':'gpu','mode':'exclusive','memory':{'minimum':0,'default':0}},'allow_overrides':[]}}
    if kind=='client':value.update(task_type='evaluation',integration_mode='sdk-command',episode_reporting=True,protocols=['http'])
    else:value.update(startup_timeout=20)
    return value


def cases():
    return [{'case_id':str(i),'task_name':'echo-'+str(i),'episodes':2,'seed':i+1,'parameters':{'variant':i,'label':'client-label','sleep_seconds':1,'fail':False}} for i in range(3)]


if __name__=='__main__':
    root=Path(__file__).parent
    for mode in ('host','docker'):
        for kind in ('client','server'):(root/f'{kind}-{mode}.json').write_text(json.dumps(template(kind,mode),indent=2)+'\n')
    (root/'suite.json').write_text(json.dumps({'schema_version':1,'name':'Scheduling acceptance: three cases','version':'v1','cases':cases()},indent=2)+'\n')
