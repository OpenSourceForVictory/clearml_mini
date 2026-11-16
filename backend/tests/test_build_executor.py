"""Build-host boundary tests: source isolation, deadlines and completion contracts."""
import io
import json
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from scripts import build_client as builder


@pytest.fixture
def build():
    return {'id':'build-1','name':'client','version':'1','token':'lease-token',
            'source':{'entrypoint':'package.client:Client','files':{'package/client.py':'class Client: pass\n','requirements.txt':''}}}


@pytest.mark.parametrize('push,build_exit,push_exit,timeout,expected',[
    (False,0,0,False,'SUCCEEDED'),(True,0,0,False,'SUCCEEDED'),
    (True,1,0,False,'FAILED'),(True,0,1,False,'FAILED'),
    (False,0,0,True,'FAILED')])
def test_build_context_and_result(monkeypatch,build,push,build_exit,push_exit,timeout,expected):
    calls=[];contexts=[]
    monkeypatch.setenv('ADMIN_API_TOKEN','private-admin-token')
    def run(argv,**kwargs):
        calls.append(argv)
        assert 'env' not in kwargs
        if argv[1]=='build':
            context=Path(argv[-1]);contexts.append(context)
            assert (context/'client/package/client.py').read_text()=='class Client: pass\n'
            recipe=(context/'Dockerfile').read_text()
            assert 'private-admin-token' not in recipe
            assert 'issubclass(client, EvaluationClient)' in recipe
            assert not (context/'client/Dockerfile').exists()
            assert kwargs['timeout']==1500
            if timeout:raise subprocess.TimeoutExpired(argv,1500)
        else:assert kwargs['timeout']==120
        kwargs['stdout'].write(b'x'*61000+b'\nlast build diagnostic\n')
        return SimpleNamespace(returncode=build_exit if argv[1]=='build' else push_exit)
    monkeypatch.setattr(builder.subprocess,'run',run)
    result=builder.build_source(build,'base:1','client:1',push)
    assert result['status']==expected and result['token']=='lease-token'
    assert result['image']==('client:1' if expected=='SUCCEEDED' else None)
    assert len(result['log'])<=60000
    assert ('deadline exceeded' in result['log']) if timeout else result['log'].endswith('last build diagnostic\n')
    assert len(calls)==(2 if push and build_exit==0 and not timeout else 1)
    assert all(not path.exists() for path in contexts)


@pytest.mark.parametrize('field,value', [('base','bad;touch /tmp/x'),('image','-bad'),('source','../escape.py'),('source','Dockerfile')])
def test_invalid_build_never_invokes_docker(monkeypatch,build,field,value):
    monkeypatch.setattr(builder.subprocess,'run',lambda *a,**kw:pytest.fail('invalid source reached Docker'))
    if field=='source':build['source']['files']={value:'bad'}
    with pytest.raises(ValueError):builder.build_source(build,value if field=='base' else 'base:1',value if field=='image' else 'client:1')


@pytest.mark.parametrize('mode',['empty','success','failure'])
def test_claim_complete_cli(monkeypatch,build,mode,capsys):
    calls=[]
    monkeypatch.setattr(sys,'argv',['build_client','--api','http://test','--push'])
    def api(base,path,body):
        calls.append((base,path,body));return {'build':None if mode=='empty' else build}
    def execute(*args):
        assert args==(build,'mini-eval-client-base:1','mini-eval-user:build-1',True)
        if mode=='failure':raise RuntimeError('build tool unavailable')
        return {'token':build['token'],'status':'SUCCEEDED','image':'image:1','log':'ok'}
    monkeypatch.setattr(builder,'api',api);monkeypatch.setattr(builder,'build_source',execute)
    builder.main()
    assert calls[0][1]=='admin/client-builds/claim'
    if mode=='empty':assert len(calls)==1 and 'No pending' in capsys.readouterr().out
    else:
        assert calls[1][1]=='admin/client-builds/build-1/complete'
        assert calls[1][2]['status']==('FAILED' if mode=='failure' else 'SUCCEEDED')
        if mode=='failure':assert calls[1][2]['image'] is None


def test_api_request(monkeypatch):
    monkeypatch.setenv('ADMIN_API_TOKEN','test-only')
    def open_request(request,timeout):
        assert timeout==30 and request.full_url=='http://build/api/v1/claim'
        assert request.get_header('Authorization')=='Bearer test-only'
        assert json.loads(request.data)=={'x':1}
        return io.BytesIO(b'{"build":null}')
    monkeypatch.setattr(builder.urllib.request,'urlopen',open_request)
    assert builder.api('http://build/','claim',{'x':1})=={'build':None}
