"""Use real children to verify bounded logs, final results, and exit status."""
import json
import os
from pathlib import Path
import sys

import pytest
from worker import host_supervisor


def launch(tmp_path,monkeypatch,script,**options):
    config={'command':[sys.executable,'-c',script],'cwd':str(tmp_path),'environment':dict(os.environ),**options}
    path=tmp_path/'launch.json';path.write_text(json.dumps(config));monkeypatch.setattr(sys,'argv',['supervisor',str(path)])
    return path


@pytest.mark.parametrize('exit_code',[0,7])
def test_supervisor_bounds_output_but_keeps_late_final_and_status(tmp_path,monkeypatch,exit_code):
    script="import sys,json\nsys.stdout.buffer.write(b'x'*300000+b'\\n'+b'\\xff\\n')\nsys.stderr.write('e'*300000)\nprint(json.dumps({'final':{'success_rate':0.5}}))\nsys.exit("+str(exit_code)+')'
    launch(tmp_path,monkeypatch,script)
    assert host_supervisor.main()==exit_code
    assert (tmp_path/'stdout.log').stat().st_size==host_supervisor.LIMIT
    assert (tmp_path/'stderr.log').stat().st_size==host_supervisor.LIMIT
    assert json.loads((tmp_path/'exit.json').read_text())['code']==exit_code
    assert json.loads((tmp_path/'final.json').read_text())['final']['success_rate']==0.5


def test_resource_verification_precedes_business_start(tmp_path,monkeypatch):
    seen=[]
    launch(tmp_path,monkeypatch,"print('started')",resource_limits={'cpus':2,'memory_bytes':1024},npu_device={'id':'test'})
    monkeypatch.setattr('worker.resource_limits.verify_current',lambda **kw:seen.append(('limits',kw)))
    monkeypatch.setattr('worker.npu_host.verify_devices',lambda device:seen.append(('npu',device)))
    assert host_supervisor.main()==0
    assert seen==[('limits',{'cpus':2,'memory_bytes':1024}),('npu',{'id':'test'})]
    assert (tmp_path/'stdout.log').read_text()=='started\n'


def test_failed_device_verification_does_not_launch_business(tmp_path,monkeypatch):
    launch(tmp_path,monkeypatch,"open('should-not-exist','w').write('bad')",npu_device={'id':'test'})
    def denied(_):raise RuntimeError('device mismatch')
    monkeypatch.setattr('worker.npu_host.verify_devices',denied)
    with pytest.raises(RuntimeError,match='device mismatch'):host_supervisor.main()
    assert not (tmp_path/'should-not-exist').exists()
    assert not (tmp_path/'exit.json').exists()
