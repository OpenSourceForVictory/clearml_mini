"""Opt-in real Docker deployment/SSH/restart test; never installs on the SSH target."""
import os
import pytest
import json,time,subprocess,urllib.request,urllib.error,uuid
pytestmark=pytest.mark.skipif(os.getenv('BUNDLED_ONBOARDING_TEST')!='1',reason='requires isolated mini-eval-onboarding-test Compose project')


def test_bundled_executor_deployment_and_recovery():
    info=json.loads(subprocess.check_output(['docker','inspect','mini-eval-onboarding-test-onboarding-1']))[0]
    assert info['Config']['Labels']['com.docker.compose.project']=='mini-eval-onboarding-test'
    from pathlib import Path
    root=Path(os.environ['BUNDLED_ONBOARDING_TEST_DIRECTORY'])
    env=dict(line.split('=',1) for line in (root/'test.env').read_text().splitlines())
    base='http://127.0.0.1:18194'
    def call(path,body=None,token=None):
        request=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
        with urllib.request.urlopen(request,timeout=10) as r:return json.load(r)
    token=call('/api/v1/auth/login',{'username':'admin','password':env['ADMIN_PASSWORD']})['access_token']
    def listing():return call('/api/v1/admin/ssh-onboarding',token=token)
    for _ in range(60):
        try:
            assert listing()['available'];break
        except urllib.error.HTTPError:time.sleep(.25)
    else:raise AssertionError('executor did not become ready')
    form=dict(host=os.environ['BUNDLED_ONBOARDING_SSH_HOST'],port=22,username='root',node_name='isolated-connectivity-probe',api_url=base,mode='host',gpus='all',password='not-used-before-fingerprint-confirmation',request_id=uuid.uuid4().hex)
    operation=call('/api/v1/admin/ssh-onboarding/check',form,token)
    for _ in range(40):
        current=next(x for x in listing()['operations'] if x['id']==operation['id'])
        if current['state']!='CHECKING':break
        time.sleep(.25)
    assert current['state']=='NEEDS_HOST_KEY',current['state']
    assert current['host_key']['fingerprint'].startswith('SHA256:')
    # No trust/confirm: no authentication or installation on target.
    container='mini-eval-onboarding-test-onboarding-1'
    subprocess.run(['docker','stop',container],check=True,stdout=subprocess.DEVNULL)
    try:
        listing()
        raise AssertionError('outage not detected')
    except urllib.error.HTTPError as exc:
        assert exc.code==503
    assert call('/healthz')['status']=='ok'
    subprocess.run(['docker','start',container],check=True,stdout=subprocess.DEVNULL)
    for _ in range(40):
        try:
            current=next(x for x in listing()['operations'] if x['id']==operation['id'])
            break
        except (urllib.error.HTTPError,StopIteration):time.sleep(.25)
    assert current['state']=='INTERRUPTED',current['state']
    assert current['error']=='executor_restarted_credentials_expired'
    # Verify Docker itself restarts the process after unexpected exit.
    before=json.loads(subprocess.check_output(['docker','inspect',container]))[0]['RestartCount']
    info=json.loads(subprocess.check_output(['docker','inspect',container]))[0]
    assert info['Name']=='/mini-eval-onboarding-test-onboarding-1' and info['State']['Running']
    subprocess.run(['docker','run','--rm','--network','none','--pid=host','--cap-drop=ALL','--cap-add=KILL','mini-eval-onboarding:dev','python','-c','import os,signal,sys; os.kill(int(sys.argv[1]),signal.SIGKILL)',str(info['State']['Pid'])],check=True,stdout=subprocess.DEVNULL)
    for _ in range(60):
        info=json.loads(subprocess.check_output(['docker','inspect',container]))[0]
        if info['RestartCount']>before and info['State'].get('Health',{}).get('Status')=='healthy':break
        time.sleep(.25)
    assert info['RestartCount']>before and listing()['available']
    result={'fresh_compose':'passed','ssh_host_key_probe':'passed (no authentication/install)','outage_api_isolation':'passed','restart_state_recovery':'passed','automatic_process_restart':'passed'}
    (root/'result.json').write_text(json.dumps(result,indent=2))
    assert all('passed' in value for value in result.values())
