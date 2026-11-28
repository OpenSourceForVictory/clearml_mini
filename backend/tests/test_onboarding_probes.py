"""Environment admission checks must fail before credentials or services are changed."""
import io
import json
import urllib.error
import urllib.request
from types import SimpleNamespace

import pytest
from onboarding.executor import Control,Operations
from onboarding.contracts import Rejected


@pytest.mark.parametrize('scenario,issue,repair',[
    ('ready',None,False),('sudo','sudo_permission_required',False),
    ('repair',None,True),('repair-denied','resource_delegation_requires_sudo',False),
    ('limits','controllers_missing',False),('disk','insufficient_installation_disk',False)])
def test_probe_resource_admission(tmp_path,monkeypatch,scenario,issue,repair):
    ops=Operations(tmp_path,'http://test')
    calls=[]
    plan={'issues':[],'services':[{'scope':'system'}] if scenario=='sudo' else [],
          'uid':1000,'free_disk_gb':0.01 if scenario=='disk' else 10}
    def run(argv,*a,**kwargs):
        calls.append(argv)
        if argv==['true']:return (1 if scenario in ('sudo','repair-denied') else 0),b'',b''
        return 0,json.dumps(plan).encode(),b''
    limits={'ready':scenario not in ('repair','repair-denied','limits'),
            'repairable':scenario in ('repair','repair-denied'),'reason':'controllers_missing'}
    monkeypatch.setattr(ops,'host_resources',lambda *a:limits)
    form={'python':'/usr/bin/python3','node_name':'test','mode':'host','gpus':'all','api_url':'http://test'}
    try:
        result=ops.probe(SimpleNamespace(run=run),form,'runner:1')
        assert result['issues']==([issue] if issue else [])
        assert bool(result.get('resource_repair_required'))==repair
        assert calls[0][:2]==['/usr/bin/python3','-c']
    finally:ops.close()


@pytest.mark.parametrize('payload',[[],{'issues':'wrong'}])
def test_invalid_probe_response(tmp_path,payload):
    ops=Operations(tmp_path,'http://test')
    remote=SimpleNamespace(run=lambda *a,**kw:(0,json.dumps(payload).encode(),b''))
    try:
        with pytest.raises(Rejected,match='invalid_probe_response'):
            ops.probe(remote,{'python':'python','node_name':'n','mode':'docker','gpus':'all','api_url':'http://test'},'runner')
    finally:ops.close()


@pytest.mark.parametrize('payload',[[],{'ready':'true'},{'ready':False},{'ready':True}])
def test_host_limit_response_requires_boolean(tmp_path,payload):
    ops=Operations(tmp_path,'http://test')
    remote=SimpleNamespace(run=lambda *a,**kw:(0,json.dumps(payload).encode(),b''))
    try:
        if isinstance(payload,dict) and type(payload['ready']) is bool:
            assert ops.host_resources(remote,{'python':'/usr/bin/python3'})==payload
        else:
            with pytest.raises(Rejected,match='invalid_resource_probe_response'):ops.host_resources(remote,{'python':'python'})
    finally:ops.close()


@pytest.mark.parametrize('scenario',['success','oversize','bad-json','network','http'])
def test_control_response_bounds_and_redacted_errors(monkeypatch,scenario):
    def build(handler):
        assert handler.redirect_request(None,None,302,'redirect',{},'http://elsewhere') is None
        def open_request(request,timeout):
            assert timeout==15 and request.get_header('Authorization')=='Bearer test-only'
            assert request.full_url=='http://test/api/v1/check' and json.loads(request.data)=={'x':1}
            if scenario=='network':raise OSError('private data')
            if scenario=='http':raise urllib.error.HTTPError(request.full_url,403,'private data',{},io.BytesIO())
            return io.BytesIO(b'x'*(4*1024*1024+1) if scenario=='oversize' else b'bad' if scenario=='bad-json' else b'{"ok":true}')
        return SimpleNamespace(open=open_request)
    monkeypatch.setattr('onboarding.executor.urllib.request.build_opener',build)
    if scenario=='success':assert Control('http://test/','Bearer test-only').call('check',{'x':1})=={'ok':True}
    else:
        expected={'oversize':'control_response_limit','bad-json':'control_api_unreachable','network':'control_api_unreachable','http':'control_api_rejected_403'}[scenario]
        with pytest.raises(Rejected,match=expected):Control('http://test','Bearer test-only').call('check',{'x':1})


@pytest.mark.parametrize('host,direct', [('api-gateway',True),('api',True),('platform.example',False)])
def test_control_internal_api_ignores_environment_proxy(monkeypatch,host,direct):
    handlers=[]
    monkeypatch.setenv('HTTP_PROXY','http://proxy.invalid:8080')

    def build(*items):
        handlers.extend(items)
        return SimpleNamespace(open=lambda request,timeout:io.BytesIO(b'{"ok":true}'))

    monkeypatch.setattr('onboarding.executor.urllib.request.build_opener',build)
    assert Control(f'http://{host}:8000','Bearer test-only').call('admin/health')=={'ok':True}
    assert len(handlers)==(2 if direct else 1)
    if direct:
        assert isinstance(handlers[1],urllib.request.ProxyHandler)
        assert handlers[1].proxies=={}


@pytest.mark.parametrize('status,body,expected_reason',[
    (503,b'{"reason":"connection_pool"}','connection_pool'),
    (503,b'{"reason":"private token"}','unavailable'),
    (503,b'not-json','unavailable'),
    (504,b'<html>gateway timeout</html>','unavailable')])
def test_control_busy_error_logs_route_without_secret(monkeypatch,caplog,status,body,expected_reason):
    def build(*handlers):
        def fail(request,timeout):
            raise urllib.error.HTTPError(request.full_url,status,'private text',{},io.BytesIO(body))
        return SimpleNamespace(open=fail)
    monkeypatch.setattr('onboarding.executor.urllib.request.build_opener',build)
    with pytest.raises(Rejected,match=f'control_api_rejected_{status}'):
        Control('http://api-gateway:8000','Bearer hidden-secret').call('admin/health')
    assert f'path=admin/health status={status} reason={expected_reason}' in caplog.text
    assert 'hidden-secret' not in caplog.text
    assert 'private text' not in caplog.text
