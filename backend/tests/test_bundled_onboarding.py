"""Default deployment contracts and real executor health transport."""
import threading
from pathlib import Path
from types import SimpleNamespace
import pytest
import yaml
from onboarding.healthcheck import check
from onboarding.server import Server, Handler

ROOT=Path(__file__).resolve().parents[2]


def test_default_deployment_is_self_contained_and_legacy_compatible():
    config=yaml.safe_load((ROOT/'deploy/compose.yml').read_text())
    api=config['services']['api'];executor=config['services']['onboarding']
    assert ':-/run/mini-eval-managed-onboarding/control.sock' in api['environment']['ONBOARDING_SOCKET']
    assert api['depends_on']['onboarding']['condition']=='service_healthy'
    assert executor['restart']=='unless-stopped'
    assert not executor.get('ports') and not executor.get('privileged')
    assert executor['environment']=={'ONBOARDING_RECONNECT_TIMEOUT_SECONDS':'${ONBOARDING_RECONNECT_TIMEOUT_SECONDS:-300}'}
    assert not executor.get('depends_on')
    assert 'onboarding_socket:/run/mini-eval-managed-onboarding:ro' in api['volumes']
    assert any('ONBOARDING_SOCKET_DIR' in x for x in api['volumes'])
    assert not any('onboarding_state' in x or 'docker.sock' in x for x in api['volumes'])
    assert 'onboarding_state:/var/lib/mini-eval-onboarding' in executor['volumes']
    assert 'docker.sock' not in str(executor)
    dockerfile=(ROOT/'onboarding/Dockerfile').read_text()
    for directory in ('onboarding','scripts','worker','node_agent'):
        assert 'COPY '+directory+'/' in dockerfile


def test_healthcheck_real_socket_and_recovery(tmp_path):
    path=str(tmp_path/'executor.sock')
    assert not check(path)
    server=Server(path,Handler);server.operations=SimpleNamespace()
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        assert check(path)  # No API, credentials or Operations.list needed.
    finally:
        server.shutdown();server.server_close();thread.join()
    assert not check(path)


@pytest.mark.parametrize('status,body,expected',[(200,b'{"ready":true}',True),(503,b'{}',False),(200,b'bad-json',False),(200,b'x'*1025,False),(200,b'{"ready":false}',False)])
def test_healthcheck_rejects_unhealthy_protocol(monkeypatch,status,body,expected):
    from onboarding import healthcheck
    closed=[]
    response=SimpleNamespace(status=status,read=lambda count:body)
    conn=SimpleNamespace(request=lambda *a:None,getresponse=lambda:response,close=lambda:closed.append(True))
    monkeypatch.setattr(healthcheck,'Connection',lambda *a,**k:conn)
    assert check('/unused') is expected
    assert closed==[True]


@pytest.mark.parametrize('ready',[True,False])
def test_healthcheck_cli_exit_status(tmp_path,monkeypatch,ready):
    import runpy
    import sys
    path=str(tmp_path/'cli.sock')
    server=Server(path,Handler);server.operations=SimpleNamespace()
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    if ready:thread.start()
    else:server.server_close()
    monkeypatch.setattr(sys,'argv',['onboarding.healthcheck',path])
    try:
        with pytest.raises(SystemExit) as result:
            runpy.run_path(str(ROOT/'onboarding/healthcheck.py'),run_name='__main__')
        assert result.value.code==(0 if ready else 1)
    finally:
        if ready:
            server.shutdown();server.server_close();thread.join()
