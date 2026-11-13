"""Probe only explicitly approved interpreters and code roots."""
import os
import subprocess
import threading
import time
from pathlib import Path
from .storage import atomic_json


def user_environment():
    uid=os.getuid()
    return {**os.environ,'XDG_RUNTIME_DIR':f'/run/user/{uid}','DBUS_SESSION_BUS_ADDRESS':f'unix:path=/run/user/{uid}/bus'}


def probe(profile):
    python=Path(profile['python']);root=Path(profile['code_root'])
    if not python.is_absolute() or not python.is_file() or not os.access(python,os.X_OK):raise ValueError('approved Python executable is missing')
    if not root.is_absolute() or not root.is_dir() or not os.access(root,os.R_OK|os.X_OK):raise ValueError('approved code root is missing')
    subprocess.run([str(python),'-c','import sys; assert sys.version_info >= (3,10)'],check=True,timeout=5,capture_output=True)
    r=subprocess.run(['systemctl','--user','show','--property=Version','--value'],env=user_environment(),capture_output=True,text=True,timeout=5)
    if r.returncode:raise ValueError('systemd user manager unavailable; enable linger for Worker user')
    if not Path('/sys/fs/cgroup/cgroup.controllers').exists():raise ValueError('cgroup v2 is required for host execution')


class HostEnvironments:
    def __init__(self,state_path):
        self.path=Path(str(state_path)+'.host-environments.json')
        import json
        self.record=json.loads(self.path.read_text()) if self.path.exists() else {'revision':0,'environments':{}}
        self.revision=self.record['revision'];self.results={};self.probed_at=0;self.thread=None
    def report(self):
        return {'revision':self.revision,'applied_revision':self.record['revision'],'environments':self.results if time.monotonic()-self.probed_at<30 else {}}
    def update(self,desired,busy):
        self.revision=desired['revision']
        if not busy and self.revision!=self.record['revision']:
            self.record=desired;atomic_json(self.path,self.record);self.probed_at=0;self.results={}
        if (self.thread and self.thread.is_alive()) or time.monotonic()-self.probed_at<10:return
        record=self.record
        def work():
            result={}
            for name,profile in record['environments'].items():
                try:probe(profile);result[name]={'ready':True,'error':None}
                except Exception as exc:result[name]={'ready':False,'error':str(exc)[:1000]}
            if record==self.record:self.results=result;self.probed_at=time.monotonic()
        self.thread=threading.Thread(target=work,daemon=True);self.thread.start()
