"""Real process matrix; Docker tests are opt-in and never contact model services."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace as N

import pytest
from backend.tests.test_host_backend import worker
from runner.process_command import prepare
from worker.host_backend import HostBackend


def programs(root):
    (root/'app.py').write_text('''import os,json
from pathlib import Path
assert Path('config.txt').read_text()=='business-data'
if os.getenv('MINI_EVAL_RESULT'):Path(os.environ['MINI_EVAL_RESULT']).write_text('{"success":true}')
print('{"final":{"success_rate":1.0,"episode_count":2,"runtime":0.01}}')
''')
    (root/'run.sh').write_text('''#!/bin/bash
set -eu
test "$(cat config.txt)" = business-data
if [ -n "${MINI_EVAL_RESULT:-}" ]; then printf '{"success":true}' > "$MINI_EVAL_RESULT"; fi
printf '{"final":{"success_rate":1.0,"episode_count":2,"runtime":0.01}}\n'
''');(root/'run.sh').chmod(0o755)
    (root/'config.txt').write_text('business-data')
    (root/'app.c').write_text('''#include <stdio.h>
#include <stdlib.h>
#include <string.h>
int main(void) {char b[64]={0};FILE*f=fopen("config.txt","r");if(!f)return 8;fread(b,1,63,f);fclose(f);if(strcmp(b,"business-data"))return 9;char*p=getenv("MINI_EVAL_RESULT");if(p){f=fopen(p,"w");if(!f)return 10;fputs("{\\"success\\":true}",f);fclose(f);}puts("{\\"final\\":{\\"success_rate\\":1.0,\\"episode_count\\":2,\\"runtime\\":0.01}}");return 0;}
''')
    subprocess.run(['cc',str(root/'app.c'),'-o',str(root/'native')],check=True,capture_output=True)


COMMANDS=[['python3','-u','app.py'],['/bin/bash','run.sh'],['./run.sh'],['./native'],['native'],['bash','-lc','exec ./native']]


@pytest.mark.parametrize('mode',['command','direct','sdk-command','service'])
@pytest.mark.parametrize('argv',COMMANDS)
def test_host_real_command_matrix(tmp_path,monkeypatch,mode,argv):
    programs(tmp_path)
    w=worker(tmp_path,argv);task=w.state['task'];spec=task['client_spec'];spec['service_mode']='none'
    template=spec['template'];template['host_runtime'].update(command_mode='exec',environment={'PATH':'.:/usr/bin:/bin'})
    template['integration_mode']='sdk-command' if mode=='sdk-command' else 'direct'
    if mode=='command':template['task_type']='command'
    if mode=='service':spec.update(workload='service',service={'port':19000,'address':'127.0.0.1'})
    monkeypatch.setattr('worker.host_backend.probe',lambda *a:None)
    backend=HostBackend(w);backend.create(task)
    launch=json.loads((backend.root/'launch.json').read_text())
    assert launch['command'][0]==sys.executable
    result=subprocess.run(launch['command'],env=launch['environment'],cwd=launch['cwd'],capture_output=True,text=True,timeout=20)
    assert result.returncode==0,result.stderr
    assert 'success_rate' in result.stdout
    if mode=='sdk-command':assert result.stdout.count('"status":"COMPLETED"')==2


def test_resolution_errors_and_argument_preservation(tmp_path):
    executable=tmp_path/'a program';executable.write_text('#!/bin/sh\nexit 0');executable.chmod(0o755)
    args=['./a program','space value','; touch evil','--flag','-u']
    assert prepare(args,tmp_path,{'PATH':'.'})==[str(executable),*args[1:]]
    assert prepare(['a program'],tmp_path,{'PATH':'.'})==[str(executable)]
    for argv in (['missing-command'],['./missing']):
        with pytest.raises(ValueError,match='executable'):prepare(argv,tmp_path,{'PATH':str(tmp_path)})
    executable.chmod(0o644)
    with pytest.raises(ValueError,match='executable'):prepare(['./a program'],tmp_path,{})
    with pytest.raises(ValueError,match='directory'):prepare(['/bin/echo'],tmp_path/'missing',{})
    assert not (tmp_path/'evil').exists()
    for argv in ([],[''],['/bin/echo','\x00']):
        with pytest.raises(ValueError):prepare(argv,tmp_path,{})
    from runner.process_command import for_template
    assert for_template({},['legacy'],None,{})==['legacy']
    assert prepare(['/bin/echo','ok'],None,{})==['/bin/echo','ok']


def test_contract_roundtrip_and_old_worker_gate():
    from backend.app.clients import TemplateCreate
    from backend.app.templates import ServerCreate
    from backend.app.image_runtime import blockers
    from backend.app.core import now
    for cls in (TemplateCreate,ServerCreate):
        for mode in ('host','docker'):
            fields={'host_runtime':{'environment_name':'test','command_mode':'exec'}} if mode=='host' else {'image':'test:local','runtime':{'command_mode':'exec','working_directory':'/app','interpreter':'/opt/platform/bin/python'}}
            template=cls(name='generic',version='1',execution_mode=mode,command=['/bin/bash','run.sh'],**fields).model_dump()
            assert template[('host_runtime' if mode=='host' else 'runtime')]['command_mode']=='exec'
            assert '14' in blockers(None,N(protocol_version=13),{'template':template},now())[0]
            from backend.app.scheduling import eligibility_codes
            assert eligibility_codes(N(protocol_version=13,disk_min_free_mb=None),None,N(client_spec={'template':template}),[],now())==['client_runtime']
    from runner.image_runtime import validate
    for directory in ('relative','/app/../root','/app\x00'):
        with pytest.raises(ValueError,match='working_directory'):validate({'working_directory':directory})


@pytest.mark.skipif(os.getenv('GENERIC_DOCKER_TEST')!='1',reason='opt-in real Docker matrix')
@pytest.mark.parametrize('mode',['command','direct','sdk-command','service'])
@pytest.mark.parametrize('argv',COMMANDS)
def test_docker_real_command_matrix(tmp_path,mode,argv):
    programs(tmp_path)
    for name in ('work','output'):(tmp_path/name).mkdir()
    spec={'contract_version':1,'task_name':'generic','seed':0,'endpoint':'','service_mode':'none','job_id':'test','execution_id':'test','episodes':2,'timeout_seconds':20,'request_timeout_seconds':2,
          'template':{'execution_mode':'docker','integration_mode':'sdk-command' if mode=='sdk-command' else 'direct','command':argv,
                      'runtime':{'command_mode':'exec','working_directory':'/case','work_dir':'/case/work','output_dir':'/case/output','environment':{'PATH':'.:/usr/local/bin:/usr/bin:/bin'}}}}
    if mode=='command':spec['template']['task_type']='command'
    module='runner.cs_entry'
    if mode=='service':spec.update(workload='service',port=19000,bind_host='127.0.0.1');module='runner.service_entry'
    from runner.image_runtime import validate
    spec['template']['runtime']=validate(spec['template']['runtime'],argv)
    (tmp_path/'input.json').write_text(json.dumps(spec))
    image=os.getenv('GENERIC_DOCKER_IMAGE','mini-eval-api:root-managed-20261117')
    result=subprocess.run(['docker','run','--rm','--user',str(os.getuid())+':'+str(os.getgid()),'--workdir','/platform','--network','none','--cap-drop','ALL','--security-opt','no-new-privileges','--memory','256m','--cpus','1',
                           '-v',str(Path.cwd()/'runner')+':/platform/runner:ro','-v',str(tmp_path)+':/case',
                           '-e','PYTHONPATH=/platform','-e','MINI_EVAL_INPUT=/case/input.json','-e','MINI_EVAL_OUTPUT_DIR=/case/output',
                           '--entrypoint','/usr/local/bin/python',image,'-m',module],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stderr
    assert 'success_rate' in result.stdout
    if mode=='sdk-command':assert result.stdout.count('"status":"COMPLETED"')==2


def test_current_worker_protocol_is_accepted_by_registration_contract():
    from backend.app.execution import Register
    from worker.version import PROTOCOL_VERSION
    registration=Register(gpu_index=0,gpu_uuid='test',gpu_model='test',vram_gb=0,protocol_version=PROTOCOL_VERSION)
    assert registration.protocol_version==PROTOCOL_VERSION
