import pytest
from onboarding.docker_prepare import prepare
from onboarding.contracts import Rejected


class Remote:
    def __init__(self,inspect=0,pull=0,build=0,probe=0):
        self.results={'inspect':inspect,'pull':pull,'build':build,'run':probe};self.calls=[]
    def run(self,args,**kwargs):
        self.calls.append((args,kwargs))
        verb=args[2] if args[1]=='image' else args[1]
        return self.results.get(verb,0),b'',b''


@pytest.mark.parametrize('present,pull,expected',[(True,0,[]),(False,0,['pull']),(False,1,['pull','build'])])
def test_image_preparation_only_on_missing(present,pull,expected):
    remote=Remote(inspect=0 if present else 1,pull=pull);stages=[]
    prepare(remote,'python3','/trusted','mini-eval-runner:dev','gpu',[0],stages.append)
    assert [c[0][1] for c in remote.calls if c[0][1] in ('pull','build')]==expected
    assert stages==['PREPARE_IMAGE','VERIFY_DOCKER']
    command=next(c[0] for c in remote.calls if c[0][1]=='run')
    assert all(x in command for x in ('--network=none','--cpus=1','--memory=128m','--pids-limit=32'))
    assert remote.calls[-1][0][1:3]==['rm','-f']


@pytest.mark.parametrize('image,kwargs,error',[('-bad',{},'runner_image_invalid'),('private/runner:1',{'inspect':1,'pull':1},'runner_image_pull_failed'),('mini-eval-runner:dev',{'inspect':1,'pull':1,'build':1},'runner_image_build_failed'),('mini-eval-runner:dev',{'probe':1},'docker_runtime_verification_failed')])
def test_failures_are_bounded_and_no_image_substitution(image,kwargs,error):
    remote=Remote(**kwargs)
    with pytest.raises(Rejected,match=error):prepare(remote,'python3','/trusted',image,'gpu',[0],lambda _:None)
    if error=='runner_image_pull_failed':assert not any(c[0][1]=='build' for c in remote.calls)
    if error=='docker_runtime_verification_failed':assert remote.calls[-1][0][1:3]==['rm','-f']


def test_npu_probes_each_assigned_device():
    remote=Remote()
    prepare(remote,'python3','/trusted','image','npu',[1,3],lambda _:None)
    commands=[c[0] for c in remote.calls if c[0][1]=='run']
    assert len(commands)==2
    for index,command in zip([1,3],commands):
        assert f'/dev/davinci{index}:/dev/davinci{index}:rw' in command
        assert '/dev/davinci_manager:/dev/davinci_manager:rw' in command
    assert commands[0][4]!=commands[1][4]
