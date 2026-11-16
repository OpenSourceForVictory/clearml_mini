"""Demo registration is isolated behind HTTP/exec boundaries."""
import io
import json
import stat
import sys

import pytest
from scripts import demo_worker


@pytest.mark.parametrize('mode',['prepare','run','once','existing'])
def test_demo_credential_creation_and_execution(tmp_path,monkeypatch,capsys,mode):
    monkeypatch.setattr(demo_worker,'ROOT',tmp_path)
    env=tmp_path/'test.env';env.write_text('ADMIN_USERNAME=test\nADMIN_PASSWORD=test-password\nAPI_BIND_PORT=18099\n#COMMENT=yes\n')
    args=['demo','--env-file',str(env)]
    if mode=='prepare':args+=['--prepare-only']
    if mode=='once':args+=['--once']
    monkeypatch.setattr(sys,'argv',args)
    config=tmp_path/'.runtime/local-mock.json'
    if mode=='existing':
        config.parent.mkdir();config.write_text('{"token":"existing-test-token"}')
    calls=[];execs=[]
    def urlopen(request,timeout):
        calls.append(request);assert timeout==10
        if request.full_url.endswith('/auth/login'):value={'access_token':'test-admin-token'}
        elif request.full_url.endswith('/pools'):value=[{'name':'mock','id':'pool'}]
        else:
            assert request.get_header('Authorization')=='Bearer test-admin-token'
            value={'token':'test-worker-token','node_name':'local-mock'}
        return io.BytesIO(json.dumps(value).encode())
    monkeypatch.setattr(demo_worker.urllib.request,'urlopen',urlopen)
    monkeypatch.setattr(demo_worker.os,'chdir',lambda directory:execs.append(directory))
    monkeypatch.setattr(demo_worker.os,'execv',lambda *args:execs.append(args))
    demo_worker.main()
    if mode=='existing':assert not calls and json.loads(config.read_text())['token']=='existing-test-token'
    else:
        assert len(calls)==3 and stat.S_IMODE(config.stat().st_mode)==0o600
        value=json.loads(config.read_text());assert value['mock'] and value['pool_id']=='pool'
    if mode=='prepare':assert not execs
    else:
        assert execs[0]==tmp_path
        executable,argv=execs[1]
        assert executable==sys.executable and argv[:3]==[sys.executable,'-m','worker.client']
        assert ('--once' in argv)==(mode=='once')
    output=capsys.readouterr().out
    assert 'test-password' not in output and 'test-worker-token' not in output
