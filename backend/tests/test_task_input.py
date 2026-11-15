import copy
import json
import subprocess
import sys

import pytest
from runner.parameter_contract import resolve_layers
from runner.task_input import build, read, bind_platform, validate_combined
from runner.image_runtime import environment
from backend.tests.test_parameter_contract import schema


def test_layer_sources_replacement_and_isolation():
    definition=schema();definition['fields']['options']['properties']['optional']={'type':'integer'}
    suite={'scene':'room','options':{'scale':1,'optional':2}}
    case={'options':{'scale':3},'trials':4}
    values,sources=resolve_layers(definition,[('suite',suite),('case',case)],{'trials':8})
    assert values=={'render':False,'scene':'room','options':{'scale':3},'trials':8}
    assert sources=={'render':'template','scene':'suite','options':'case','trials':'override'}
    suite['options']['scale']=999;case['trials']=99
    assert values['options']=={'scale':3} and values['trials']==8
    server,_=resolve_layers(definition,[('service',{'scene':'server'})])
    assert server['scene']=='server' and server['trials']==2


@pytest.mark.parametrize('layers,overrides',[
    ([('case',{'scene':'room','trials':True})],{'trials':4}),
    ([('case',{'scene':'room','endpoint':'http://bad'})],{}),
    ([('case',{'scene':'room'})],{'scene':'other'}),
    ([('template',{'scene':'room'})],{}),
    ([('override',{'scene':'room'})],{}),
    ([('case',{'scene':'room'}),('case',{})],{}),
])
def test_invalid_layer_cannot_be_hidden(layers,overrides):
    with pytest.raises(ValueError):resolve_layers(schema(),layers,overrides)


@pytest.mark.parametrize('key',['CUDA_VISIBLE_DEVICES','ASCEND_VISIBLE_DEVICES','ROCR_VISIBLE_DEVICES','MINI_EVAL_ENDPOINT'])
def test_platform_environment_is_reserved(key):
    with pytest.raises(ValueError,match='reserved'):environment({key:'x'})


def config():
    return {'contract_version':1,'job_id':'job-1','execution_id':'exec-1','case_id':'case-1',
            'task_name':'scene','seed':5,'episodes':2,'endpoint':'ws://192.168.8.1:9000',
            'parameters':{'message':'hello'},'template':{'command':['program']}}


def test_input_envelope_preserves_legacy_and_resolved_host_paths(tmp_path):
    c=config();original=copy.deepcopy(c)
    c['_resolved_assets']={'assets:data':'/mnt/node/data'}
    document=build(c,{'MINI_EVAL_WORK_DIR':'/mnt/node/job/work','MINI_EVAL_OUTPUT_DIR':'/mnt/node/job/output'})
    assert document['platform']['paths']['assets']=={'data':'/mnt/node/data'}
    assert document['platform']['paths']['work']=='/mnt/node/job/work'
    assert document['business']=={'message':'hello'}
    assert document['endpoint']==document['platform']['service']['endpoint']==original['endpoint']
    document['business']['message']='changed'
    assert c['parameters']['message']=='hello'
    path=tmp_path/'input.json';path.write_text(json.dumps(document))
    assert read(path)==document


def test_no_service_and_service_listen_are_distinct():
    c=config();c.pop('endpoint');assert build(c,{})['platform']['service'] is None
    c.update(workload='service',bind_host='192.168.8.1',port=19093)
    value=build(c,{})
    assert value['platform']['listen']=={'address':'192.168.8.1','port':19093}
    assert value['platform']['service'] is None


def test_platform_binding_is_explicit_shell_free_and_checked(tmp_path):
    c=config();c['task_name']='scene; touch '+str(tmp_path/'unsafe')
    mappings=[{'source':'platform.service.endpoint','target':'argv','name':'--server'},
              {'source':'task.task_name','target':'argv','name':'--task'},
              {'source':'task.seed','target':'env','name':'BUSINESS_SEED'}]
    document=build(c,{})
    argv,env=bind_platform(mappings,document,[sys.executable,'-c','import json,sys;print(json.dumps(sys.argv[1:]))'])
    result=subprocess.run(argv,check=True,text=True,capture_output=True,timeout=5)
    assert json.loads(result.stdout)==['--server',c['endpoint'],'--task',c['task_name']]
    assert env=={'BUSINESS_SEED':'5'} and not (tmp_path/'unsafe').exists()
    with pytest.raises(ValueError,match='conflicts'):bind_platform(mappings,document,['program'],{'BUSINESS_SEED':'bad'})
    c.pop('endpoint')
    with pytest.raises(ValueError,match='unavailable'):bind_platform(mappings,build(c,{}),['program'])
    with pytest.raises(ValueError,match='duplicate'):
        validate_combined(schema(),[{'source':'task.task_name','target':'argv','name':'--scene'}])


@pytest.mark.parametrize('value',[True,2,'1',None])
def test_input_version_is_strict(tmp_path,value):
    path=tmp_path/'input.json';path.write_text(json.dumps({'input_version':value}))
    with pytest.raises(ValueError,match='version'):read(path)


def test_legacy_sdk_and_dry_run(tmp_path):
    from runner.sdk import Context
    c=config();c['template']['platform_bindings']=[{'source':'platform.service.endpoint','target':'argv','name':'--server'}]
    context=Context(c,tmp_path)
    assert context.business=={'message':'hello'} and context.service_endpoint==c['endpoint']
    path=tmp_path/'input.json';path.write_text(json.dumps(c))
    result=subprocess.run([sys.executable,'-m','runner.task_input',str(path),'--dry-run'],check=True,capture_output=True,text=True,timeout=5)
    assert json.loads(result.stdout)=={'valid':True,'argv':['program','--server',c['endpoint']],'environment':{}}


def test_docker_publication_path_updates_command_and_environment(tmp_path):
    from runner.task_input import publication_path
    from runner.image_runtime import resolve
    c=config();c['template'].update(execution_mode='docker',runtime={'work_dir':str(tmp_path/'work')},command=['program','--input','${input_path}'])
    path=publication_path(c,'/tmp/root-owned-input.json')
    command,environment,_=resolve(c,input_path=path)
    assert path==str(tmp_path/'work'/'.mini-eval-input.json')
    assert command[-1]==environment['MINI_EVAL_INPUT']==path
    c['template']['execution_mode']='host'
    assert publication_path(c,'/approved/input.json')=='/approved/input.json'
