"""Isolated API plus two real systemd user Worker units; never production nodes."""
import json,os,re,shutil,subprocess,sys,time,urllib.request
from pathlib import Path
from worker.updater import Updater
from worker.version import SOFTWARE_VERSION


def main():
    from worker.host_environments import user_environment
    os.environ.update(user_environment())
    base='http://127.0.0.1:18093/api/v1/';token=None
    def api(path,body=None,method=None):
        req=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),method=method,headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
        with urllib.request.urlopen(req,timeout=10) as r:return json.load(r)
    def wait(fn):
        until=time.monotonic()+40
        while time.monotonic()<until:
            if result:=fn():return result
            time.sleep(.5)
        raise RuntimeError('temporary workers did not connect')
    token=api('auth/login',{'username':'admin','password':os.environ['UPGRADE_TEST_PASSWORD']})['access_token']
    root=Path('.runtime/worker-upgrades')/str(time.time_ns());root=root.resolve();root.mkdir(parents=True)
    app=root/'app';app.mkdir()
    for package in ('worker','runner'):shutil.copytree(package,app/package,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    version=app/'worker/version.py'
    previous_version='0.0.0' if SOFTWARE_VERSION!='0.0.0' else '0.0.1'
    source,count=re.subn(r'^SOFTWARE_VERSION\s*=.*$', 'SOFTWARE_VERSION='+repr(previous_version), version.read_text(), flags=re.MULTILINE)
    if count!=1:raise RuntimeError('cannot prepare isolated Worker version fixture')
    version.write_text(source)
    n=api('admin/worker-credentials',{'node_name':'upgrade-test-'+root.name,'roles':['client'],'max_concurrent':2})
    pool=api('admin/pools',{'name':'upgrade-test-'+root.name,'gpu_model':'virtual'})
    units=[];configs=[];unitbytes={};configbytes={};unitdir=Path.home()/'.config/systemd/user';unitdir.mkdir(parents=True,exist_ok=True)
    try:
        for i in range(2):
            config=root/f'gpu{i}.json';cfg={'api_url':base.removesuffix('/api/v1/'),'token':n['token'],'node_name':n['node_name'],'pool_id':pool['id'],'gpu_index':i,'mock':True,'runner_image':'mini-eval-runner:dev','state_path':str(root/f'state{i}.json'),'workspace_root':str(root/f'work{i}'),'min_free_disk_gb':0}
            config.write_text(json.dumps(cfg));config.chmod(0o600);configs.append(str(config));configbytes[str(config)]=config.read_bytes()
            name=f'mini-eval-worker-upgrade-test-{root.name}-gpu{i}.service';unit=unitdir/name;units.append(unit)
            text='[Unit]\nDescription=Isolated worker upgrade acceptance\nRequiresMountsFor='+str(root)+'\n[Service]\nType=simple\nWorkingDirectory='+str(app)+'\nExecStart='+sys.executable+' -m worker.client --config '+str(config)+'\nRestart=always\nRestartSec=1\nTimeoutStopSec=25\n'
            unit.write_text(text);unitbytes[str(unit)]=unit.read_bytes()
        subprocess.run(['systemctl','--user','daemon-reload'],check=True)
        for unit in units:subprocess.run(['systemctl','--user','start',unit.name],check=True)
        route='admin/nodes/'+n['id']+'/upgrades'
        before=wait(lambda:(v if len((v:=api(route))['slots'])==2 and all(s['online'] for s in v['slots']) else None))
        assert {s['software']['version'] for s in before['slots']}=={previous_version}
        cfg={'app_dir':str(app),'state_dir':str(root/'upgrade-state'),'config_paths':configs,'services':[{'scope':'user','name':u.name} for u in units],'verify_timeout':30}
        updater=Updater(cfg);updater.step()
        release=api('admin/worker-releases',{})
        operation=api(route,{'expected_revision':0,'release_id':release['id']})
        updater.step()
        after=api(route);assert after['state']=='SUCCEEDED',after
        assert {s['id'] for s in before['slots']}=={s['id'] for s in after['slots']}
        assert all(s['software']['version']==SOFTWARE_VERSION and s['software']['build_digest']==release['id'] for s in after['slots'])
        for path,data in configbytes.items():assert Path(path).read_bytes()==data
        for path,data in unitbytes.items():assert Path(path).read_bytes()==data
        (root/'evidence.json').write_text(json.dumps({'upgrade':operation['id'],'release':release['id'],'slots':after['slots'],'units_preserved':True,'credentials_preserved':True},indent=2))
        print('PASS two real Worker units upgraded; GPU identities, credentials, recovery paths and original mount dependencies preserved')
    finally:
        for unit in units:
            subprocess.run(['systemctl','--user','stop',unit.name],check=False,capture_output=True);unit.unlink(missing_ok=True)
        subprocess.run(['systemctl','--user','daemon-reload'],check=True)

if __name__=='__main__':main()
