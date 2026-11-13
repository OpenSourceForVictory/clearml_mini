"""Isolated API, two real user Worker services and a standalone updater process."""
import base64
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from scripts import install_node
from worker.host_environments import user_environment


def main():
    os.environ.update(user_environment())
    base='http://127.0.0.1:18093/api/v1/';token=None
    def api(path,body=None):
        request=urllib.request.Request(base+path,data=None if body is None else json.dumps(body).encode(),headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
        with urllib.request.urlopen(request,timeout=10) as r:return json.load(r)
    def wait(fn):
        until=time.monotonic()+45
        while time.monotonic()<until:
            value=fn()
            if value:return value
            time.sleep(.5)
        raise RuntimeError('node components did not reconnect')
    token=api('auth/login',{'username':'admin','password':os.environ['UPGRADE_TEST_PASSWORD']})['access_token']
    root=(Path('.runtime/node-enrollment')/str(time.time_ns())).resolve();root.mkdir(parents=True)
    app=root/'app';app.mkdir();configs=root/'configs';configs.mkdir()
    for package in ('worker','runner'):shutil.copytree(package,app/package,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    node=api('admin/worker-credentials',{'node_name':'reenroll-'+root.name,'max_concurrent':2})
    prefix='admin/nodes/'+node['id'];unitdir=Path.home()/'.config/systemd/user'
    units=[];paths=[];originals={};agent=None
    try:
        for index in range(2):
            path=configs/f'gpu{index}.json';value={'api_url':base.removesuffix('/api/v1/'),'node_name':node['node_name'],'token':node['token'],'pool_id':None,'gpu_index':index,'mock':True,'runner_image':'mini-eval-runner:dev','state_path':str(root/f'state{index}.json'),'workspace_root':str(root/f'work{index}'),'min_free_disk_gb':0}
            path.write_text(json.dumps(value));path.chmod(0o600);paths.append(str(path));originals[str(path)]=value
            unit=unitdir/f'mini-eval-worker-enrollment-{root.name}-{index}.service';units.append(unit)
            unit.write_text('[Unit]\nRequiresMountsFor='+str(root)+'\n[Service]\nWorkingDirectory='+str(app)+'\nExecStart='+sys.executable+' -m worker.client --config '+str(path)+'\nRestart=always\nRestartSec=1\nTimeoutStopSec=25\n')
        snapshots={u:u.read_bytes() for u in units}
        subprocess.run(['systemctl','--user','daemon-reload'],check=True)
        for u in units:subprocess.run(['systemctl','--user','start',u.name],check=True)
        before=wait(lambda:(v if len((v:=api(prefix+'/upgrades'))['slots'])==2 and all(x['online'] for x in v['slots']) else None))
        api(prefix+'/unmanage',{'expected_revision':0})
        credential=api(prefix+'/reenroll',{'expected_revision':1})
        credential['api_url']=base.removesuffix('/api/v1/')
        package=api('admin/node-installation-bundle');bundle=root/'bundle';bundle.mkdir()
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(package['archive']))) as z:z.extractall(bundle)
        layout={'app_dir':str(app),'state_dir':str(root/'updater-state'),'config_paths':paths,'services':[{'scope':'user','name':u.name} for u in units]}
        install_node.ROOT=bundle
        def install_agent(cfg,system):
            nonlocal agent
            assert not system
            # Run the copied standalone executable, outside the replaceable app.
            target=root/'standalone';target.mkdir();shutil.copyfile(bundle/'worker/updater.py',target/'updater.py')
            config=target/'config.json';config.write_text(json.dumps(cfg));config.chmod(0o600)
            agent=subprocess.Popen([sys.executable,str(target/'updater.py'),'--config',str(config)],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        install_node.install_updater=install_agent
        # Isolated updater process; never touch the machine's real updater unit.
        install_node.control_updater=lambda *a,**kw:None
        install_node.replace_existing(layout,credential)
        ready=wait(lambda:(v if (v:=api(prefix+'/enrollment'))['ready'] else None))
        api(prefix+'/enrollment/complete',{'expected_revision':ready['revision']})
        after=api(prefix+'/upgrades')
        assert {x['id'] for x in before['slots']}=={x['id'] for x in after['slots']}
        assert all(x['software']['build_digest']==credential['release_id'] for x in after['slots'])
        assert all(u.read_bytes()==data for u,data in snapshots.items())
        for path,old in originals.items():assert json.loads(Path(path).read_text())=={**old,'token':credential['token']}
        (root/'evidence.json').write_text(json.dumps({'node_id':node['id'],'slots':after['slots'],'updater_ready':ready['updater_ready'],'units_and_directories_preserved':True},indent=2))
        print('PASS: two real Worker services re-enrolled with original identities, copied standalone updater verified, configs and mount dependencies preserved.')
    finally:
        if agent:agent.terminate();agent.wait(timeout=20)
        for u in units:
            subprocess.run(['systemctl','--user','stop',u.name],capture_output=True);u.unlink(missing_ok=True)
        subprocess.run(['systemctl','--user','daemon-reload'],check=True)

if __name__=='__main__':main()
