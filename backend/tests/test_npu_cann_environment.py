"""Installed CANN setup is applied to host business processes without a login shell."""
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from worker import npu_host


def account():
    return SimpleNamespace(pw_uid=os.getuid(),pw_gid=os.getgid(),pw_name='business',pw_dir='/home/business')


def test_installed_setup_merges_paths_and_filters_unapproved_variables(tmp_path,monkeypatch):
    script=tmp_path/'set_env.sh'
    script.write_text('export LD_LIBRARY_PATH="/installed/lib:${LD_LIBRARY_PATH}"\n'
                      'export ASCEND_HOME_PATH=/installed/cann\n'
                      'export ASCEND_RT_VISIBLE_DEVICES=7\nexport UNRELATED_SECRET=ignored\n')
    monkeypatch.setattr(npu_host,'CANN_SETUP_FILES',(script,))
    original=subprocess.run
    def run_without_identity(args,**kwargs):
        # Exercise the actual shell setup; privilege-drop arguments are checked separately.
        for key in ('user','group','extra_groups'):kwargs.pop(key)
        return original(args,**kwargs)
    monkeypatch.setattr(npu_host.subprocess,'run',run_without_identity)
    result=npu_host.installed_environment(account(),{'PATH':'/usr/bin:/bin','HOME':'/home/business',
                                                       'LD_LIBRARY_PATH':'/template/lib'})
    assert result['LD_LIBRARY_PATH']=='/installed/lib:/template/lib'
    assert result['ASCEND_HOME_PATH']=='/installed/cann'
    assert 'ASCEND_RT_VISIBLE_DEVICES' not in result
    assert 'UNRELATED_SECRET' not in result


def test_installed_setup_runs_as_business_user_and_hides_script_output(tmp_path,monkeypatch):
    script=tmp_path/'set_env.sh';script.write_text('')
    monkeypatch.setattr(npu_host,'CANN_SETUP_FILES',(script,))
    monkeypatch.setattr(npu_host.os,'getgrouplist',lambda *a:[24,25])
    seen=[]
    def fake_run(args,**kwargs):
        seen.append((args,kwargs));return SimpleNamespace(stdout=b'LD_LIBRARY_PATH=/cann/lib\0ASCEND_RT_VISIBLE_DEVICES=3\0')
    monkeypatch.setattr(npu_host.subprocess,'run',fake_run)
    result=npu_host.installed_environment(account(),{'PATH':'/usr/bin:/bin'})
    args,kwargs=seen[0]
    assert args[-1]==str(script) and args[0]=='/bin/bash'
    assert kwargs['user']==os.getuid() and kwargs['group']==os.getgid()
    assert kwargs['extra_groups']==[24,25] and kwargs['timeout']==10
    assert kwargs['capture_output'] and kwargs['check']
    assert result=={'LD_LIBRARY_PATH':'/cann/lib'}


def test_missing_setup_preserves_explicit_configuration(tmp_path,monkeypatch):
    monkeypatch.setattr(npu_host,'CANN_SETUP_FILES',(tmp_path/'absent',))
    assert npu_host.installed_environment(account(),{'LD_LIBRARY_PATH':'/approved/lib'})=={}


@pytest.mark.parametrize('stdout,exception,expected',[
    (b'PATH=/bin\0',None,'no LD_LIBRARY_PATH'),
    (b'LD_LIBRARY_PATH=\xff\0',None,'invalid text'),
    (None,subprocess.CalledProcessError(2,['bash']),'setup failed'),
])
def test_bad_setup_fails_before_business_start(tmp_path,monkeypatch,stdout,exception,expected):
    script=tmp_path/'set_env.sh';script.write_text('')
    monkeypatch.setattr(npu_host,'CANN_SETUP_FILES',(script,))
    def fake_run(*args,**kwargs):
        if exception:raise exception
        return SimpleNamespace(stdout=stdout)
    monkeypatch.setattr(npu_host.subprocess,'run',fake_run)
    with pytest.raises(RuntimeError,match=expected):npu_host.installed_environment(account(),{'PATH':'/bin'})
