import copy
import json
import subprocess
import sys

import pytest
from runner.parameter_contract import validate_schema, resolve_values, bind_values


def schema():
    return {'version':1,'fields':{
        'scene':{'type':'string','required':True,'binding':{'target':'argv','name':'--scene'}},
        'trials':{'type':'integer','min':1,'max':100,'default':2,'overridable':True},
        'render':{'type':'boolean','default':False,'binding':{'target':'argv','name':'--render','encoding':'flag'}},
        'cameras':{'type':'array','items':{'type':'string'},'binding':{'target':'argv','name':'--camera','encoding':'repeat'}},
        'options':{'type':'object','properties':{'scale':{'type':'number','required':True}},'binding':{'target':'env','name':'BUSINESS_OPTIONS','encoding':'json'}},
    }}


def test_defaults_overrides_and_snapshot_do_not_alias():
    original=schema(); value={'scene':'room','options':{'scale':1}}
    result=resolve_values(original,value,{'trials':8})
    assert result=={'scene':'room','trials':8,'render':False,'options':{'scale':1}}
    value['options']['scale']=99; original['fields']['trials']['default']=88
    assert result['options']['scale']==1 and result['trials']==8
    with pytest.raises(ValueError,match='override'): resolve_values(schema(),{'scene':'room'},{'scene':'another'})


@pytest.mark.parametrize('values',[
 {},{'scene':None},{'scene':'room','trials':True},{'scene':'room','trials':0},
 {'scene':'room','trials':101},{'scene':'room','unknown':1},
 {'scene':'room','options':{}},{'scene':'room','options':{'scale':float('nan')}},
 {'scene':'room','options':{'scale':1,'extra':2}},
 {'scene':'room','cameras':[1]},{'scene':'room','render':1},
])
def test_invalid_business_values_are_rejected(values):
    with pytest.raises(ValueError):resolve_values(schema(),values)


def test_bindings_preserve_argv_boundary_and_never_execute_shell(tmp_path):
    payload=f'room with spaces; touch {tmp_path}/unexpected $(echo injected)'
    values=resolve_values(schema(),{'scene':payload,'render':True,'cameras':['left eye','right'],'options':{'scale':.5}})
    argv,env=bind_values(schema(),values,[sys.executable,'-c','import sys,json;print(json.dumps(sys.argv[1:]))'])
    completed=subprocess.run(argv,capture_output=True,text=True,check=True,timeout=5)
    assert json.loads(completed.stdout)==['--scene',payload,'--render','--camera','left eye','--camera','right']
    assert not (tmp_path/'unexpected').exists()
    assert json.loads(env['BUSINESS_OPTIONS'])=={'scale':.5}
    assert '--render' not in bind_values(schema(),{'scene':'room'},['program'])[0]


@pytest.mark.parametrize('dest',['CUDA_VISIBLE_DEVICES','MINI_EVAL_INPUT','NVIDIA_VISIBLE_DEVICES','PYTHONPATH'])
def test_business_bindings_cannot_override_platform_environment(dest):
    s=schema();s['fields']['scene']['binding']={'target':'env','name':dest}
    with pytest.raises(ValueError,match='reserved'):validate_schema(s)


def test_duplicate_binding_and_runtime_env_conflicts():
    s=schema();s['fields']['second']=copy.deepcopy(s['fields']['scene'])
    with pytest.raises(ValueError,match='duplicate'):validate_schema(s)
    with pytest.raises(ValueError,match='conflicts'):
        bind_values(schema(),{'scene':'room','options':{'scale':1}},['program'],{'BUSINESS_OPTIONS':'existing'})


@pytest.mark.parametrize('version',[True,0,2,'1'])
def test_protocol_version_is_strict(version):
    s=schema();s['version']=version
    with pytest.raises(ValueError):validate_schema(s)


def test_enum_keeps_boolean_distinct_from_integer_and_rejects_nonfinite():
    s={'version':1,'fields':{'choice':{'type':'enum','enum':[1,'one']}}}
    assert resolve_values(s,{'choice':1})=={'choice':1}
    with pytest.raises(ValueError):resolve_values(s,{'choice':True})
    s['fields']['choice']['enum']=[float('inf')]
    with pytest.raises(ValueError):validate_schema(s)


def test_missing_optional_value_is_not_null_or_empty():
    s={'version':1,'fields':{'name':{'type':'string'}}}
    assert resolve_values(s,{})=={}
    assert resolve_values(s,{'name':''})=={'name':''}
    with pytest.raises(ValueError):resolve_values(s,{'name':None})


def test_reserved_names_and_invalid_defaults_are_rejected():
    for field in ['seed','episodes','task_name','work_dir','MINI_EVAL_CUSTOM']:
        with pytest.raises(ValueError):validate_schema({'version':1,'fields':{field:{'type':'string'}}})
    s=schema();s['fields']['trials']['default']=0
    with pytest.raises(ValueError):validate_schema(s)


def test_errors_do_not_echo_business_values():
    secret='private-token-not-for-errors'
    with pytest.raises(ValueError) as err:resolve_values(schema(),{'scene':'room','trials':secret})
    assert secret not in str(err.value) and 'trials' in str(err.value)

@pytest.mark.parametrize('target,dest',[('argv','--task-config'),('env','task-config'),('env','TASK_CONFIG'),('input',None)])
def test_hyphenated_business_parameters_reach_real_process(tmp_path,target,dest):
    binding={'target':target,**({'name':dest} if dest else {})}
    spec={'version':1,'fields':{'task-config':{'type':'string','default':'demo_clean','overridable':True,'binding':binding},
                              'task_config':{'type':'string','default':'distinct'}}}
    values=resolve_values(spec,{}, {'task-config':'demo clean;$(not-executed)'})
    assert values=={'task-config':'demo clean;$(not-executed)','task_config':'distinct'}
    command=[sys.executable,'-c','import os,sys,json;print(json.dumps([sys.argv[1:],dict(os.environ)]))']
    argv,env=bind_values(spec,values,command)
    import os
    result=subprocess.run(argv,env={**os.environ,**env},capture_output=True,text=True,check=True,timeout=5)
    args,environment=json.loads(result.stdout)
    if target=='argv':assert args==['--task-config',values['task-config']]
    if target=='env':assert environment[dest]==values['task-config']
    if target=='input':assert args==[] and 'task-config' not in env

def test_hyphenated_object_properties_and_name_boundaries():
    spec={'version':1,'fields':{'task-config':{'type':'object','properties':{'control-mode':{'type':'string'}}}}}
    assert resolve_values(spec,{'task-config':{'control-mode':'eef'}})=={'task-config':{'control-mode':'eef'}}
    for name in ['a'+'-'*63,'task_config']:
        validate_schema({'version':1,'fields':{name:{'type':'string'}}})
    for name in ['-task','1task','task.config','task config','a'*65,'a\0b']:
        with pytest.raises(ValueError):validate_schema({'version':1,'fields':{name:{'type':'string'}}})
    from runner.parameter_contract import NAME
    assert not NAME.fullmatch('asset-name')

@pytest.mark.parametrize('name',['MINI_EVAL_foo-bar','ASCEND_foo-bar','CUDA_VISIBLE_DEVICES','bad=name','bad name','a\0b','-bad','a'*129])
def test_hyphen_environment_still_rejects_reserved_or_invalid_names(name):
    from runner.image_runtime import environment
    with pytest.raises(ValueError):environment({name:'value'})

def test_hyphen_environment_template_overrides_keep_exact_names():
    from runner.template_contract import host_config
    from runner.image_runtime import environment
    assert environment({'task-config':'demo_clean','task_config':'other'})=={'task-config':'demo_clean','task_config':'other'}
    config=host_config({'environment_name':'approved','environment':{'task-config':'demo_clean'},'overridable_env':['task-config']})
    assert config['overridable_env']==['task-config']
