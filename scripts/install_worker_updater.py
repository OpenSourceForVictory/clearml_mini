"""Install the independent updater without editing any Worker service/config."""
import argparse,json,os,shutil,subprocess,sys
from pathlib import Path


def main():
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--system',action='store_true');p.add_argument('--start',action='store_true');p.add_argument('--update',action='store_true');a=p.parse_args()
    if a.system and os.geteuid()!=0:p.error('--system requires root')
    if not a.system:
        os.environ.setdefault('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}')
        os.environ.setdefault('DBUS_SESSION_BUS_ADDRESS','unix:path='+os.environ['XDG_RUNTIME_DIR']+'/bus')
    source=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(source))
    from worker.updater import Updater
    value=json.loads(a.config.read_text());Updater(value)
    root=Path('/opt/mini-eval-updater') if a.system else Path.home()/'.local/share/mini-eval-updater'
    unit_dir=Path('/etc/systemd/system') if a.system else Path.home()/'.config/systemd/user'
    unit=unit_dir/'mini-eval-updater.service'
    if (root.exists() or unit.exists()) and not a.update:p.error('updater already exists; stop it and use --update')
    if a.update:
        result=subprocess.run(['systemctl']+([] if a.system else ['--user'])+['is-active','mini-eval-updater.service'],capture_output=True)
        if result.returncode==0:p.error('stop the updater before replacing its code')
    root.mkdir(parents=True,exist_ok=True);unit_dir.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(source/'worker/updater.py',root/'updater.py')
    config=root/'config.json';config.write_text(json.dumps(value,indent=2));config.chmod(0o600)
    quote=lambda x:json.dumps(str(x).replace('%','%%'))
    unit.write_text('[Unit]\nDescription=Mini Eval node upgrade executor\nAfter=network-online.target\n\n[Service]\nType=simple\nExecStart='+quote(sys.executable)+' '+quote(root/'updater.py')+' --config '+quote(config)+'\nRestart=always\nRestartSec=5\nTimeoutStopSec=45\n\n[Install]\nWantedBy='+('multi-user.target' if a.system else 'default.target')+'\n')
    if a.start:
        base=['systemctl']+([] if a.system else ['--user']);subprocess.run(base+['daemon-reload'],check=True);subprocess.run(base+['enable','--now','mini-eval-updater.service'],check=True)
    print('Installed independent updater; existing Worker units and credentials unchanged.')

if __name__=='__main__':main()
