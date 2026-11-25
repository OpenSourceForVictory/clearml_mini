"""Real isolated runtime launch-response loss, not production batch validation."""
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

import pytest

from worker.host_environments import user_environment
from worker.recovery_launch import RecoveryLaunch


@pytest.mark.skipif(os.getenv('RECOVERY_RUNTIME_TEST')!='1',reason='real isolated launch opt-in')
@pytest.mark.parametrize('kind',['host','docker'])
def test_real_launch_response_loss_recovers_same_runtime(tmp_path,kind):
    execution=str(uuid.uuid4());name='mini-eval-launch-test-'+execution
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    args=dict(execution_id=execution,generation='g',request_id=str(uuid.uuid4()),boot_id=boot,kind=kind)
    j=RecoveryLaunch(tmp_path/'launch',**args)
    request=j.request(time.monotonic())
    j.accept(dict(execution_id=execution,**request,action='launch',reason=None,duplicate=False,valid_for_seconds=30),time.monotonic())
    commands=[]
    def command(argv,check=True):
        return subprocess.run(argv,env=user_environment(),capture_output=True,text=True,timeout=20,check=check)
    def start(marker):
        commands.append(marker)
        if kind=='host':
            command(['systemd-run','--user','--quiet','--unit='+name,'--property=RemainAfterExit=yes',
                '--property=Description=mini-eval-launch:'+marker,'/usr/bin/python3','-c','import time;time.sleep(2)'])
        else:
            command(['docker','run','--detach','--name',name,'--label','mini-eval.launch='+marker,
                '--entrypoint','python',os.getenv('RECOVERY_RUNTIME_IMAGE','mini-eval-api:recovery-foundations-20261125'),
                '-c','import time;time.sleep(2)'])
        raise ConnectionError('injected lost startup response after real spawn')
    def inspect():
        if kind=='host':
            data=dict(line.split('=',1) for line in command(['systemctl','--user','show',name+'.service',
                '--property=Description,InvocationID,ActiveState,ExecMainStatus']).stdout.splitlines() if '=' in line)
            marker=data['Description'].removeprefix('mini-eval-launch:')
            return {'launch_id':marker,'identity':dict(kind=kind,boot_id=boot,runtime_id=data['InvocationID'])},data
        data=json.loads(command(['docker','inspect',name]).stdout)[0]
        return {'launch_id':data['Config']['Labels']['mini-eval.launch'],
                'identity':dict(kind=kind,boot_id=boot,runtime_id=data['Id'])},data
    try:
        with pytest.raises(ConnectionError):j.start(time.monotonic(),start)
        original_started=j.record['started_at'];j.close();j=RecoveryLaunch(tmp_path/'launch',**args)
        observed,_=inspect();record=j.bind(observed)
        assert record['started_at']==original_started
        with pytest.raises(RuntimeError):j.start(time.monotonic(),start)
        assert len(commands)==1
        end=time.monotonic()+8
        while time.monotonic()<end:
            after,data=inspect()
            done=(data.get('ActiveState')=='active' and data.get('ExecMainStatus')=='0') if kind=='host' else not data['State']['Running']
            # systemd ActiveState can be active while sleeping; use SubState below.
            if kind=='host':
                done=command(['systemctl','--user','show',name+'.service','--property=SubState','--value']).stdout.strip()=='exited'
            if done:break
            time.sleep(.05)
        assert done and after==observed
        assert (data['ExecMainStatus'] if kind=='host' else data['State']['ExitCode']) in ('0',0)
        j.bind(after)
    finally:
        j.close()
        if kind=='host':
            command(['systemctl','--user','stop',name+'.service'],check=False)
            command(['systemctl','--user','reset-failed',name+'.service'],check=False)
        else:command(['docker','rm','--force',name],check=False)


@pytest.mark.skipif(os.getenv('RECOVERY_RUNTIME_TEST')!='1',reason='real production runtime backend opt-in')
@pytest.mark.parametrize('kind',['host','docker'])
def test_actual_runtime_backend_launch_marker_survives_lost_response(tmp_path,kind):
    from backend.tests.test_host_backend import worker
    from worker.host_backend import HostBackend
    from worker.client import Worker,docker
    from worker.recovery_runtime import RecoveryRuntime
    w=worker(tmp_path,['python3','business.py'],timeout=0)
    w.host_mode=lambda:kind=='host'
    task=w.state['task'];execution=task['execution_id'];name='mini-eval-'+execution
    boot=Path('/proc/sys/kernel/random/boot_id').read_text().strip()
    args=dict(execution_id=execution,generation='g',request_id=str(uuid.uuid4()),boot_id=boot,kind=kind)
    j=RecoveryLaunch(tmp_path/'recovery-launch',**args)
    marker=j.record['launch_id'];task['recovery_launch_id']=marker
    host=HostBackend(w)
    script='import time;print("start",flush=True);time.sleep(2);print("done",flush=True)'
    try:
        if kind=='host':
            (tmp_path/'business.py').write_text(script)
            host.create(task)
            path=host.root/'launch.json';data=json.loads(path.read_text())
            data['command']=['/usr/bin/python3',str(tmp_path/'business.py')];path.write_text(json.dumps(data))
        else:
            docker('create','--name',name,'--label','mini-eval.execution='+execution,'--label','mini-eval.launch='+marker,
                   '--entrypoint','python',os.getenv('RECOVERY_RUNTIME_IMAGE','mini-eval-api:recovery-foundations-20261125'),'-c',script)
            w.name=lambda e:'mini-eval-'+e;w.inspect=lambda e:Worker.inspect(w,e)
        runtime=RecoveryRuntime(w,execution)
        request=j.request(time.monotonic())
        j.accept(dict(execution_id=execution,**request,action='launch',reason=None,duplicate=False,valid_for_seconds=30),time.monotonic())
        seen=[]
        def lost_response(token):
            seen.append(runtime.start(token));raise ConnectionError('lost response')
        with pytest.raises(ConnectionError):j.start(time.monotonic(),lost_response)
        original=j.record['started_at'];j.close();j=RecoveryLaunch(tmp_path/'recovery-launch',**args)
        observed=runtime.inspect_launch(marker);assert observed==seen[0]
        assert j.bind(observed)['started_at']==original
        with pytest.raises(RuntimeError):j.start(time.monotonic(),lost_response)
        with pytest.raises(RuntimeError):runtime.start(marker)
        assert len(seen)==1
        end=time.monotonic()+8
        while runtime.inspect()['state']=='running' and time.monotonic()<end:time.sleep(.05)
        assert runtime.inspect()['exit_code']==0
        assert runtime.inspect_launch(marker)==observed
    finally:
        j.close()
        if kind=='host':host.remove(execution)
        else:docker('rm','--force',name,check=False)
