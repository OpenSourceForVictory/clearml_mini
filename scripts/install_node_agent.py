"""Install the node Agent beside existing Worker code without rewriting Worker units."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


def install(layout, start=False):
    root=Path(__file__).resolve().parents[1]
    sys.path.insert(0,str(root))
    from node_agent.programs import Programs
    from node_agent.state import local_directory
    from worker.storage import atomic_json
    Programs(layout['services'])
    scopes={x['scope'] for x in layout['services']}
    if len(scopes)!=1:raise ValueError('mixed service scopes are unsupported')
    system='system' in scopes
    if system and os.geteuid()!=0:raise ValueError('system Agent requires root')
    app=Path(layout['app_dir']).resolve()
    state=local_directory(Path(layout['state_dir']).parent/'mini-eval-agent')
    values=[json.loads(Path(p).read_text()) for p in layout['config_paths']]
    if len({(v['node_name'],v['api_url'],v['token']) for v in values})!=1:raise ValueError('Worker identities differ')
    unit_dir=Path('/etc/systemd/system') if system else Path.home()/'.config/systemd/user'
    unit_dir.mkdir(parents=True,exist_ok=True)
    base=['systemctl']+([] if system else ['--user'])
    if start:
        subprocess.run(base+['stop','mini-eval-agent.service'],capture_output=True,timeout=30)
    target=app/'node_agent'
    if root/'node_agent'!=target:
        shutil.copytree(root/'node_agent',target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__'))
    record=app/'.worker-release.json'
    digest=json.loads(record.read_text()).get('sha256','') if record.exists() else ''
    config={k:values[0][k] for k in ('api_url','token','node_name')}
    config.update(state_dir=str(state),services=layout['services'],digest=digest)
    config_path=state/'config.json';atomic_json(config_path,config)
    quote=lambda x:json.dumps(str(x).replace('%','%%'))
    unit='[Unit]\nDescription=Mini Eval node Agent\nAfter=network-online.target\n\n[Service]\nType=simple\nWorkingDirectory='+quote(app)+'\nExecStart='+quote(sys.executable)+' -m node_agent.client --config '+quote(config_path)+'\nRestart=on-failure\nRestartSec=5\nTimeoutStopSec=20\nUMask=0077\n\n[Install]\nWantedBy='+('multi-user.target' if system else 'default.target')+'\n'
    (unit_dir/'mini-eval-agent.service').write_text(unit)
    layout['agent']={'scope':'system' if system else 'user','name':'mini-eval-agent.service','state_dir':str(state),'config_path':str(config_path)}
    if start:
        subprocess.run(base+['daemon-reload'],check=True,timeout=30)
        subprocess.run(base+['enable','--now','mini-eval-agent.service'],check=True,timeout=30)
    return layout


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--config',type=Path,required=True);p.add_argument('--start',action='store_true');a=p.parse_args()
    install(json.loads(a.config.read_text()),a.start)


if __name__=='__main__':main()
