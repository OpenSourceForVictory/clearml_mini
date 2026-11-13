"""Standalone updater: install this file outside the Worker code directory."""
import argparse
import fcntl
import hashlib
import io
import json
import os
from pathlib import Path,PurePosixPath
import re
import shutil
import subprocess
import tempfile
import time
import urllib.request
import urllib.error
import zipfile
from contextlib import ExitStack

MAX_ARCHIVE=2*1024*1024


def atomic(path,value):
    path=Path(path);temporary=path.with_suffix('.tmp')
    with temporary.open('w') as f:
        os.chmod(temporary,0o600);json.dump(value,f);f.flush();os.fsync(f.fileno())
    os.replace(temporary,path)
    directory=os.open(path.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(directory)
    finally:os.close(directory)


def unpack(payload,digest,target):
    if len(payload)>MAX_ARCHIVE or hashlib.sha256(payload).hexdigest()!=digest:raise ValueError('release digest mismatch')
    with zipfile.ZipFile(io.BytesIO(payload)) as archive:
        files=archive.infolist()
        if len(files)>512 or sum(x.file_size for x in files)>8*1024*1024:raise ValueError('release exceeds extraction limits')
        seen=set()
        for info in files:
            p=PurePosixPath(info.filename)
            if p.is_absolute() or '..' in p.parts or len(p.parts)<2 or p.parts[0] not in ('worker','runner','node_agent') or p.suffix!='.py' or str(p)!=info.filename or info.filename in seen or (info.external_attr>>16)&0o170000==0o120000:
                raise ValueError('invalid release member')
            seen.add(info.filename)
            data=archive.read(info);compile(data,info.filename,'exec')
            dest=target/str(p);dest.parent.mkdir(parents=True,exist_ok=True);dest.parent.chmod(0o755);dest.write_bytes(data);dest.chmod(0o644)
        if not {'worker/client.py','worker/version.py','runner/sdk.py'}<=seen:raise ValueError('incomplete release')
    (target/'.worker-release.json').write_text(json.dumps({'sha256':digest}));(target/'.worker-release.json').chmod(0o644)


class API:
    def __init__(self,config):
        self.base=config['api_url'].rstrip('/')+'/api/v1/';self.token=config['token']
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*a,**kw):return None
        self.opener=urllib.request.build_opener(NoRedirect())
    def request(self,path,body=None,binary=False,get=False):
        req=urllib.request.Request(self.base+path,data=None if binary or get else json.dumps(body or {}).encode(),headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        with self.opener.open(req,timeout=10) as response:
            data=response.read(MAX_ARCHIVE+1)
            if len(data)>MAX_ARCHIVE:raise ValueError('response too large')
            return data if binary else json.loads(data)


class Updater:
    def __init__(self,config):
        if any(not Path(config[k]).is_absolute() for k in ('app_dir','state_dir')) or any(not Path(p).is_absolute() for p in config['config_paths']):raise ValueError('updater paths must be absolute')
        self.config=config;self.app=Path(config['app_dir']).absolute();self.root=Path(config['state_dir']).absolute()
        recovering=(self.root/'upgrade.json').is_file()
        if self.app.is_symlink() or self.app.resolve()!=self.app or (not (self.app/'worker/client.py').is_file() and not recovering):raise ValueError('app_dir must be an existing non-symlink Worker installation')
        if self.root.resolve()!=self.root:raise ValueError('updater state must not contain symlinks')
        if self.root.is_relative_to(self.app):raise ValueError('updater state must be outside app_dir')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.journal=self.root/'upgrade.json';self.configs=[Path(x).absolute() for x in config['config_paths']]
        if not self.configs or len(set(self.configs))!=len(self.configs):raise ValueError('explicit distinct worker configs required')
        self.values=[json.loads(p.read_text()) for p in self.configs]
        if len({(v['api_url'],v['token'],v['node_name']) for v in self.values})!=1:raise ValueError('all GPU instances must belong to one node')
        for p,v in zip(self.configs,self.values):
            if not Path(v['state_path']).is_absolute():raise ValueError('Worker recovery paths must be absolute')
            if p.is_relative_to(self.app) or Path(v['state_path']).absolute().is_relative_to(self.app):raise ValueError('credentials and recovery state must be outside app_dir')
        if not config.get('services'):raise ValueError('existing service units must be configured')
        for unit in config['services']:
            if unit.get('scope') not in ('user','system') or not re.fullmatch(r'mini-eval-worker[\w@.\-]*\.service',unit['name']):raise ValueError('invalid Worker service unit')
        if not .01<=config.get('verify_timeout',120)<=600:raise ValueError('verify_timeout must be bounded')
        agent=config.get('agent')
        if agent and (agent.get('name')!='mini-eval-agent.service' or agent.get('scope') not in ('user','system') or not Path(agent.get('state_dir','')).is_absolute() or not Path(agent.get('config_path','')).is_absolute()):
            raise ValueError('invalid node Agent configuration')
        self.api=API(self.values[0])
        self.control_timeout=30

    def control(self,action):
        units=list(self.config['services'])
        if self.config.get('agent'):units.append(self.config['agent'])
        if action=='stop':units=list(reversed(units))
        for unit in units:
            args=['systemctl']+(['--user'] if unit['scope']=='user' else [])+[action,unit['name']]
            env=dict(os.environ)
            if unit['scope']=='user':
                env.setdefault('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}')
                env.setdefault('DBUS_SESSION_BUS_ADDRESS','unix:path='+env['XDG_RUNTIME_DIR']+'/bus')
            subprocess.run(args,check=True,capture_output=True,timeout=self.control_timeout,env=env)

    def idle_locks(self,stack,indices):
        if self.config.get('agent'):
            handle=stack.enter_context((Path(self.config['agent']['state_dir'])/'control.lock').open('a'))
            fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if sorted(v.get('gpu_index',0) for v in self.values)!=sorted(indices):raise ValueError('configured GPU instances differ from platform')
        # Catch omitted local instances sharing this installation.
        for parent in {p.parent for p in self.configs}:
            install=stack.enter_context((parent/'.install.lock').open('a'));fcntl.flock(install,fcntl.LOCK_EX|fcntl.LOCK_NB)
            for p in parent.glob('*.json'):
                try:v=json.loads(p.read_text())
                except (ValueError,OSError):continue
                if isinstance(v,dict) and v.get('state_path') and p not in self.configs:raise ValueError('unlisted local Worker configuration')
        for v in self.values:
            state=Path(v['state_path']);state.parent.mkdir(parents=True,exist_ok=True)
            handle=stack.enter_context(open(str(state)+'.lock','a'));fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            journals=[state,*Path(str(state)+'.runs').glob('*.json')]
            if any(p.exists() and json.loads(p.read_text()) for p in journals):raise ValueError('execution recovery journal remains')

    def report(self,identity,status,error=None):
        return self.api.request('worker/upgrades/'+identity+'/report',{'status':status,**({'error':error} if error else {})})

    def apply(self,job):
        identity=job['id'];record=json.loads(self.journal.read_text()) if self.journal.exists() else None
        if record and record['id']!=identity:raise ValueError('unfinished local upgrade requires recovery')
        if record and record['phase']=='done' and record.get('error')!='rollback_failed':
            self.report(identity,record['status'],record.get('error'));self.journal.unlink();return
        if record:
            # Interrupted transactions roll back; never reapply an uncertain swap.
            self.rollback(record);return
        payload=self.api.request('worker/upgrades/'+identity+'/archive',binary=True)
        stage=Path(tempfile.mkdtemp(prefix='.mini-eval-stage-',dir=self.app.parent));stage.chmod(0o755)
        backup=self.app.parent/('.mini-eval-backup-'+identity)
        try:
            unpack(payload,job['release']['id'],stage)
            if self.config.get('agent') and not (stage/'node_agent/client.py').is_file():
                raise ValueError('release does not contain node Agent')
            owner=self.app.stat();os.chown(stage,owner.st_uid,owner.st_gid)
            record={'id':identity,'phase':'prepared','stage':str(stage),'backup':str(backup),'status':None}
            atomic(self.journal,record)
            self.control('stop')
            with ExitStack() as stack:
                self.idle_locks(stack,job['gpu_indices'])
                if backup.exists():raise ValueError('backup already exists')
                record['phase']='swapping';atomic(self.journal,record)
                os.replace(self.app,backup);os.replace(stage,self.app)
                record['phase']='installed';atomic(self.journal,record)
            self.control('start');self.report(identity,'VERIFYING')
            deadline=time.monotonic()+self.config.get('verify_timeout',120)
            while time.monotonic()<deadline:
                try:
                    self.report(identity,'SUCCEEDED')
                    record.update(phase='done',status='SUCCEEDED');atomic(self.journal,record);self.journal.unlink()
                    return  # Keep the old code backup for operator rollback/audit.
                except (urllib.error.URLError,TimeoutError):time.sleep(2)
            raise RuntimeError('new version did not reconnect')
        except Exception:
            if record:self.rollback(record)
            else:
                try:self.report(identity,'FAILED','upgrade_failed')
                finally:shutil.rmtree(stage,ignore_errors=True)

    def rollback(self,record):
        # Fence rollback while admission is still blocked. A lost success ACK
        # cannot make us replace code on a node that is already scheduling.
        state=self.api.request('worker/upgrades/'+record['id']+'/state',get=True)
        if state['status']=='SUCCEEDED':
            self.journal.unlink(missing_ok=True);return
        self.report(record['id'],'ROLLING_BACK')
        record['phase']='rolling_back';atomic(self.journal,record)
        try:
            self.control('stop')
            with ExitStack() as stack:
                self.idle_locks(stack,[v.get('gpu_index',0) for v in self.values])
                backup=Path(record['backup'])
                if backup.exists():
                    if self.app.exists():
                        failed=self.app.parent/('.mini-eval-failed-'+record['id'])
                        if failed.exists():shutil.rmtree(failed)
                        os.replace(self.app,failed)
                    os.replace(backup,self.app)
            self.control('start')
            record.update(phase='done',status='FAILED',error='upgrade_failed')
        except Exception:
            record.update(phase='done',status='FAILED',error='rollback_failed')
        atomic(self.journal,record)
        self.report(record['id'],record['status'],record['error'])
        if record['error']=='rollback_failed':return
        self.journal.unlink()
        stage=Path(record['stage'])
        if stage.exists():shutil.rmtree(stage)

    def step(self):
        job=self.api.request('worker/upgrades/poll',{'agent_digest':hashlib.sha256(Path(__file__).read_bytes()).hexdigest()})['upgrade']
        if self.journal.exists():
            record=json.loads(self.journal.read_text())
            if record['phase']=='done' and record.get('error')!='rollback_failed':
                state=self.api.request('worker/upgrades/'+record['id']+'/state',get=True)
                if state['status'] not in ('SUCCEEDED','FAILED','CANCELLED'):self.report(record['id'],record['status'],record.get('error'))
                self.journal.unlink()
                if job and job['id']==record['id']:return
            if job is None and self.journal.exists():
                # Success response may have been lost. Retry its receipt first;
                # the API treats reports for the same terminal state idempotently.
                if record['phase']=='done' and record.get('error')!='rollback_failed':
                    self.report(record['id'],record['status'],record.get('error'));self.journal.unlink();return
                state=self.api.request('worker/upgrades/'+record['id']+'/state',get=True)
                if state['status']=='SUCCEEDED':self.journal.unlink();return
                # Do not replace files after an operation has changed server-side.
                raise RuntimeError('local journal needs administrator reconciliation')
        if job:self.apply(job)


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--config',type=Path,required=True);parser.add_argument('--once',action='store_true');args=parser.parse_args()
    updater=Updater(json.loads(args.config.read_text()))
    with (updater.root/'updater.lock').open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        while True:
            try:updater.step()
            except Exception:print('Worker updater could not finish this cycle; journal retained',flush=True)
            if args.once:return
            time.sleep(5)

if __name__=='__main__':main()
