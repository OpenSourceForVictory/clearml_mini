import copy
import pytest
from runner.resource_contract import validate,resolve,request
CONTRACT={'version':1,'cpu':{'minimum':1,'default':2},'memory':{'minimum':2,'default':4},'accelerator':{'type':'gpu','mode':'shared','memory':{'minimum':2,'default':4}},'allow_overrides':['cpus','ram_gb','requested_vram_gb']}
DEFAULTS={'cpus':8,'ram_gb':16,'gpu_mode':'exclusive','requested_vram_gb':0,'required_gpu_model':None,'accelerator_type':'gpu'}

def test_layer_precedence_and_snapshot():
 c=copy.deepcopy(CONTRACT);r,s=resolve(c,[('submission',{'cpus':3}),('case',{'cpus':4})],DEFAULTS)
 assert r['cpus']==4 and r['ram_gb']==4 and s['cpus']=='case' and s['ram_gb']=='template'
 assert c==CONTRACT

@pytest.mark.parametrize('value',[0,False,None,1.5,'2',10001])
def test_invalid_cpu(value):
 with pytest.raises(ValueError):resolve(CONTRACT,[('submission',{'cpus':value})],DEFAULTS)

@pytest.mark.parametrize('values',[{'ram_gb':1},{'gpu_mode':'exclusive'},{'accelerator_type':'npu'},{'requested_vram_gb':0},{'required_gpu_model':'A100'}])
def test_minimum_and_locked(values):
 with pytest.raises(ValueError):resolve(CONTRACT,[('submission',values),('case',{'cpus':4})],DEFAULTS)

def test_legacy_explicit_zero_and_null_model():
 result,_=resolve(None,[('submission',{'requested_vram_gb':0,'required_gpu_model':None})],DEFAULTS)
 assert result==DEFAULTS
 with pytest.raises(ValueError):request({'gpu_mode':'shared','accelerator_mode':'exclusive'})
 assert request({'accelerator_memory_gb':3})=={'requested_vram_gb':3}

@pytest.mark.parametrize('change',[{'version':True},{'allow_overrides':['unknown']},{'cpu':{'minimum':3,'default':2}},{'accelerator':{'type':'npu','mode':'shared','memory':{'minimum':0,'default':0}}}])
def test_invalid_contract(change):
 with pytest.raises(ValueError):validate({**CONTRACT,**change})

@pytest.mark.parametrize('flag,axis,minimum',[('cpu_unlimited','cpus',1),('memory_unlimited','ram_gb',2)])
def test_unlimited_reserves_template_minimum(flag,axis,minimum):
 result,sources=resolve(CONTRACT,[('submission',{flag:True})],DEFAULTS)
 assert result[axis]==minimum and sources[axis]=='template_minimum'
 result,_=resolve(CONTRACT,[('submission',{flag:True,axis:8})],DEFAULTS)
 assert result[axis]==8
 locked={**CONTRACT,'allow_overrides':[]}
 with pytest.raises(ValueError,match='locked'):resolve(locked,[('submission',{flag:True})],DEFAULTS)
 with pytest.raises(ValueError,match='positive scheduling minimum'):resolve(None,[('submission',{flag:True})],DEFAULTS)
 result,_=resolve(None,[('submission',{flag:True,axis:8})],DEFAULTS)
 assert result[axis]==8
 with pytest.raises(ValueError,match='below template minimum'):resolve(CONTRACT,[('submission',{flag:True,'ram_gb':1})],DEFAULTS)

@pytest.mark.parametrize('value',[0,1,'true',None])
def test_unlimited_flags_are_strict_booleans(value):
 with pytest.raises(ValueError,match='boolean'):request({'cpu_unlimited':value})
