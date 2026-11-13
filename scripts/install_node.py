"""Install a complete node bundle. Existing service units are never rewritten."""
import argparse
from contextlib import ExitStack
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from worker.updater import API, Updater, atomic, unpack
from scripts.install_worker import identity


def existing_layout(home, services):
    configs = sorted((home/'.config/mini-eval').glob('*.json'))
    configs = [p for p in configs if 'state_path' in json.loads(p.read_text())]
    if not configs: raise ValueError('no installed Worker configs found; use new installation mode')
    if not services: raise ValueError('existing installation requires --service scope:unit for every original Worker service')
    units = []
    for value in services:
        scope, name = value.split(':', 1)
        units.append({'scope':scope, 'name':name})
    if len({x['scope'] for x in units}) != 1: raise ValueError('mixed system/user service scopes are not supported')
    return {'app_dir':str(home/'.local/share/mini-eval-worker'),
            'state_dir':str(home/'.local/state/mini-eval-updater'),
            'config_paths':[str(p) for p in configs], 'services':units}


def control_updater(system, action, check=True):
    return subprocess.run(['systemctl']+([] if system else ['--user'])+[action,'mini-eval-updater.service'],
                          check=check, capture_output=True, timeout=40)


def install_updater(layout, system):
    with tempfile.TemporaryDirectory(prefix='mini-eval-updater-') as directory:
        config = Path(directory)/'config.json'; atomic(config, layout)
        command = [sys.executable,str(ROOT/'scripts/install_worker_updater.py'),'--config',str(config),'--update','--start']
        if system: command.append('--system')
        subprocess.run(command, check=True, timeout=90, capture_output=True)


def replace_existing(layout, credential):
    updater = Updater(layout)
    if updater.journal.exists(): raise ValueError('unfinished upgrade journal; recover it before re-enrollment')
    if any(v['node_name'] != credential['node_name'] for v in updater.values):
        raise ValueError('credential does not match installed node identity')
    identity(credential)
    remote = API(credential).request('worker/self', get=True)
    if remote.get('enrollment_state') != 'INSTALLING' or remote.get('enabled'):
        raise ValueError('start re-enrollment in the platform before replacing an existing installation')
    indices = remote.get('gpu_indices', [])
    if sorted(indices) != sorted(v.get('gpu_index',0) for v in updater.values):
        raise ValueError('all original GPU instances must be included')
    digest = (ROOT/'release.sha256').read_text().strip()
    if remote.get('release_id') != digest: raise ValueError('download the bundle for the current re-enrollment operation')
    system = layout['services'][0]['scope'] == 'system'
    if system and os.geteuid() != 0: raise ValueError('system service installation requires root')
    if not system and os.geteuid() == 0: raise ValueError('run user-service installation as its owning user')
    # A separate updater lock prevents replacing a running updater, even without systemd.
    control_updater(system, 'stop', check=False)
    backup = updater.app.with_name(updater.app.name+'.before-enrollment-'+uuid.uuid4().hex)
    stage = Path(tempfile.mkdtemp(prefix='.node-install-', dir=updater.app.parent)); stage.chmod(0o755)
    originals = {p:p.read_bytes() for p in updater.configs}
    swapped = False
    try:
        unpack((ROOT/'worker-release.zip').read_bytes(), digest, stage)
        owner = updater.app.stat()
        for p in [stage,*stage.rglob('*')]: os.chown(p, owner.st_uid, owner.st_gid)
        with ExitStack() as stack:
            handle=stack.enter_context((updater.root/'updater.lock').open('a'))
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            updater.control('stop')
            updater.idle_locks(stack, indices)
            os.replace(updater.app, backup); swapped = True
            os.replace(stage, updater.app)
            for path, previous in zip(updater.configs, updater.values):
                owner = path.stat()
                # Preserve every node-local setting, replacing only connection credentials.
                atomic(path, {**previous, 'api_url':credential['api_url'], 'token':credential['token']})
                os.chown(path, owner.st_uid, owner.st_gid)
        install_updater(layout, system)
        updater.control('enable')
        updater.control('start')
    except Exception:
        if swapped:
            # Services must be stopped and journals checked before restoring code.
            control_updater(system, 'stop', check=False)
            updater.control('stop')
            with ExitStack() as stack:
                updater.idle_locks(stack, indices)
                failed = updater.app.with_name(updater.app.name+'.failed-enrollment-'+uuid.uuid4().hex)
                if updater.app.exists(): os.replace(updater.app, failed)
                os.replace(backup, updater.app)
                for path, value in originals.items():
                    owner=path.stat(); atomic(path,json.loads(value));os.chown(path,owner.st_uid,owner.st_gid)
        raise
    finally:
        if stage.exists(): shutil.rmtree(stage)
    return layout


def main():
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--credential',type=Path,required=True)
    p.add_argument('--existing',action='store_true')
    p.add_argument('--home',type=Path,default=Path.home())
    p.add_argument('--service',action='append',default=[],help='scope:original-unit.service; repeat for all units')
    p.add_argument('--instance',default='gpu')
    p.add_argument('--gpus',default='all')
    a=p.parse_args()
    os.environ.setdefault('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}')
    os.environ.setdefault('DBUS_SESSION_BUS_ADDRESS','unix:path='+os.environ['XDG_RUNTIME_DIR']+'/bus')
    try:
        credential=json.loads(a.credential.read_text())
        if a.existing:
            layout=replace_existing(existing_layout(a.home.resolve(),a.service),credential)
        else:
            if a.home!=Path.home() or a.service: raise ValueError('custom home/services require --existing')
            if list((a.home/'.config/mini-eval').glob('*.json')): raise ValueError('existing Worker configuration; use --existing')
            subprocess.run([sys.executable,str(ROOT/'scripts/install_worker.py'),'--config',str(a.credential),
                            '--instance',a.instance,'--gpus',a.gpus],check=True,timeout=120)
            paths=sorted((a.home/'.config/mini-eval').glob(a.instance+'-gpu*.json'))
            layout=existing_layout(a.home,['user:mini-eval-worker@'+x.stem+'.service' for x in paths])
            receipt=Path(layout['app_dir'])/'.worker-release.json'
            receipt.write_text(json.dumps({'sha256':(ROOT/'release.sha256').read_text().strip()}));receipt.chmod(0o644)
            install_updater(layout,False)
            Updater(layout).control('enable')
            Updater(layout).control('start')
        print('Installed Worker, runner/SDK and independent updater. Verify all GPU instances and updater in the platform before completing enrollment.')
    except ValueError as error:
        p.exit(1,'Node installation stopped: '+str(error)+'\n')
    except (OSError,subprocess.SubprocessError,KeyError):
        # Never echo a subprocess response/config that can contain node credentials.
        p.exit(1,'Node installation failed; keep the node disabled. Check identity, bundle version, original service scope, GPU configs and local recovery journals before retrying.\n')


if __name__=='__main__': main()
