"""Bounded operation service. Secrets exist only in memory and expire on restart."""
import base64
from concurrent.futures import ThreadPoolExecutor
import copy
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import urllib.request
import urllib.error
import uuid
from .contracts import validate, public_form, Rejected
from .ssh import Remote
from scripts.deploy_node import validate_bundle

ACTIVE={'CHECKING','NEEDS_HOST_KEY','READY','INSTALLING','VERIFYING'}


class Control:
    def __init__(self,base,authorization):self.base=base.rstrip('/')+'/api/v1/';self.authorization=authorization
    def call(self,path,body=None):
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*a,**kw):return None
        req=urllib.request.Request(self.base+path,data=None if body is None else json.dumps(body).encode(),headers={'Authorization':self.authorization,'Content-Type':'application/json'})
        try:
            with urllib.request.build_opener(NoRedirect()).open(req,timeout=15) as r:
                payload=r.read(4*1024*1024+1)
                if len(payload)>4*1024*1024:raise Rejected('control_response_limit')
                return json.loads(payload)
        except urllib.error.HTTPError as exc:raise Rejected('control_api_rejected_'+str(exc.code)) from None
        except (OSError,ValueError):raise Rejected('control_api_unreachable') from None


class Operations:
    def __init__(self,root,api_url,remote_factory=Remote,clock=time.time):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.api_url=api_url;self.remote_factory=remote_factory;self.clock=clock
        self.lock=threading.RLock();self.jobs={};self.secrets={};self.pool=ThreadPoolExecutor(max_workers=2)
        for path in self.root.glob('*.json'):
            value=json.loads(path.read_text())
            if value['state'] in ACTIVE:value.update(state='INTERRUPTED',error='executor_restarted_credentials_expired')
            self.jobs[value['id']]=value;self.persist(value)
    def persist(self,value):
        target=self.root/(value['id']+'.json');tmp=target.with_suffix('.tmp')
        with tmp.open('w') as f:os.chmod(tmp,0o600);json.dump(value,f);f.flush();os.fsync(f.fileno())
        os.replace(tmp,target)
    def update(self,identity,state=None,stage=None,**values):
        with self.lock:
            job=self.jobs[identity]
            if state and state not in ACTIVE:self.secrets.pop(identity,None)
            if state:job['state']=state
            if stage:job['stage']=stage
            job.update(values,updated_at=self.clock());self.persist(job)
            if job['state'] not in ACTIVE:self.secrets.pop(identity,None)
            return copy.deepcopy(job)
    def expire(self):
        with self.lock:
            for identity,job in list(self.jobs.items()):
                if job['state'] in ('READY','NEEDS_HOST_KEY') and self.clock()>job['expires_at']:self.update(identity,'EXPIRED',error='credentials_expired')
                if job['state'] not in ACTIVE and self.clock()-job['updated_at']>86400:
                    self.jobs.pop(identity);(self.root/(identity+'.json')).unlink(missing_ok=True)
    def list(self,actor):
        self.expire()
        with self.lock:return {'available':True,'operations':[copy.deepcopy(x) for x in sorted(self.jobs.values(),key=lambda x:x['created_at'],reverse=True) if x['actor']==actor]}
    def own(self,identity,actor):
        self.expire()
        if identity not in self.jobs or self.jobs[identity]['actor']!=actor:raise Rejected('operation_not_found')
        return self.jobs[identity]
    def start(self,actor,form,authorization):
        form=validate(form)
        with self.lock:
            self.expire()
            if form.get('request_id'):
                existing=next((x for x in self.jobs.values() if x['actor']==actor and x['form'].get('request_id')==form['request_id']),None)
                if existing:return copy.deepcopy(existing)
            if len(self.jobs)>=128 or sum(x['state'] in ACTIVE for x in self.jobs.values())>=8:raise Rejected('onboarding_queue_full')
            key=(form['host'],form['port'],form['username'])
            if any(x['state'] in ACTIVE and ((x['form']['host'],x['form']['port'],x['form']['username'])==key or x['form']['node_name']==form['node_name']) for x in self.jobs.values()):raise Rejected('target_has_active_operation')
            identity=uuid.uuid4().hex;t=self.clock()
            job={'id':identity,'actor':actor,'state':'CHECKING','stage':'SSH','created_at':t,'updated_at':t,'expires_at':t+900,'form':public_form(form),'error':None}
            self.jobs[identity]=job;self.secrets[identity]={'form':form,'authorization':authorization};self.persist(job)
            self.pool.submit(self.check,identity)
            return copy.deepcopy(job)
    def trust(self,identity,actor,fingerprint):
        with self.lock:
            job=self.own(identity,actor)
            if job['state']!='NEEDS_HOST_KEY' or fingerprint!=job['host_key']['fingerprint']:raise Rejected('host_key_confirmation_mismatch')
            self.secrets[identity]['form']['fingerprint']=fingerprint
            job['form']['fingerprint']=fingerprint
            self.update(identity,'CHECKING','SSH');self.pool.submit(self.check,identity)
            return copy.deepcopy(job)
    def host_resources(self, remote, form):
        source=(Path(__file__).resolve().parents[1]/'worker/resource_limits.py').read_text()
        program="import json; source="+repr(source)+"; scope={'__name__':'resource_check'}; exec(source,scope); print(json.dumps(scope['verify_host'](source)))"
        _,data,_=remote.run([form['python'],'-c',program],timeout=60)
        value=json.loads(data)
        if not isinstance(value,dict) or type(value.get('ready')) is not bool:
            raise Rejected('invalid_resource_probe_response')
        return value

    def probe(self,remote,form,runner_image):
        source=Path(__file__).with_name('probe.py').read_text()
        opts={k:form[k] for k in ('node_name','mode','gpus','api_url')};opts['node_id']=form.get('node_id');opts['runner_image']=runner_image
        _,data,_=remote.run([form['python'],'-c',source],json.dumps(opts).encode()+b'\n',timeout=120)
        plan=json.loads(data)
        if not isinstance(plan,dict) or not isinstance(plan.get('issues'),list):raise Rejected('invalid_probe_response')
        if plan['services'] and plan['services'][0]['scope']=='system':
            if remote.run(['true'],sudo=True,check=False)[0]:plan['issues'].append('sudo_permission_required')
        if form['mode']=='host' and not plan['issues']:
            limits=self.host_resources(remote,form)
            plan['resource_limits']=limits
            if not limits['ready']:
                if limits.get('repairable') and plan.get('uid',0)>0:
                    if remote.run(['true'],sudo=True,check=False)[0]:plan['issues'].append('resource_delegation_requires_sudo')
                    else:plan['resource_repair_required']=True
                else:plan['issues'].append(limits.get('reason','runtime_limits_not_enforced'))
        if plan['free_disk_gb']<.1:plan['issues'].append('insufficient_installation_disk')
        return plan
    def check(self,identity):
        try:
            secret=self.secrets[identity];form=secret['form'];remote=self.remote_factory(form)
            if not form.get('fingerprint'):
                try:key=remote.open(authenticate=False)
                finally:remote.close()
                self.update(identity,'NEEDS_HOST_KEY','HOST_KEY',host_key=key);return
            control=Control(self.api_url,secret['authorization'])
            # Recover identity after a previous failed/ack-lost attempt, never an active managed node.
            nodes=control.call('admin/nodes')
            original=next((n for n in nodes if n['node_name']==form['node_name']),None)
            if original and not form.get('node_id'):
                if original['status'] not in ('enrolling','unmanaged'):raise Rejected('node_already_managed')
                form.update(node_id=original['id'],expected_revision=original['management_revision'])
                self.update(identity,form=public_form(form))
            package=control.call('admin/node-installation-bundle')
            secret['package']=package
            runner=control.call('admin/health')['runner_image'];secret['runner_image']=runner
            self.update(identity,stage='ENVIRONMENT')
            with self.remote_factory(form) as remote:plan=self.probe(remote,form,runner)
            self.update(identity,'CHECK_FAILED' if plan['issues'] else 'READY','ENVIRONMENT',plan=plan,release_id=package['release_id'])
        except Exception as error:self.fail(identity,error)
    def confirm(self,identity,actor):
        with self.lock:
            job=self.own(identity,actor)
            if job['state'] in ('INSTALLING','VERIFYING','SUCCEEDED'):return copy.deepcopy(job)
            if job['state']!='READY':raise Rejected('environment_check_required')
            self.update(identity,'INSTALLING','RECHECK');self.pool.submit(self.install,identity)
            return copy.deepcopy(job)
    def cancel(self,identity,actor):
        with self.lock:
            job=self.own(identity,actor)
            if job['state'] not in ('READY','NEEDS_HOST_KEY'):raise Rejected('cannot_cancel_active_installation')
            return self.update(identity,'CANCELLED',error=None)
    def fail(self,identity,error):
        # No raw exception, remote stdout/stderr, password, key or token is persisted.
        code=str(error) if isinstance(error,Rejected) else 'onboarding_step_failed'
        self.update(identity,'FAILED',error=code)
    def install(self,identity):
        remote_dir=None
        try:
            secret=self.secrets[identity];form=secret['form'];package=secret['package'];control=Control(self.api_url,secret['authorization'])
            payload=base64.b64decode(package['archive']);validate_bundle(payload,package['sha256'])
            with self.remote_factory(form) as remote:
                plan=self.probe(remote,form,secret['runner_image'])
                prior=self.jobs[identity]['plan']
                if plan['issues'] or any(plan[k]!=prior[k] for k in ('home','python','gpus','selected_gpus','configs','services','existing')):raise Rejected('environment_changed_repeat_check')
                devices=[{'index':x['index'],'uuid':x['uuid']} for x in plan['gpus'] if x['index'] in plan['selected_gpus']]
                self.update(identity,stage='IDENTITY')
                credential=control.call('admin/ssh-enrollment/begin',{'node_id':form.get('node_id') or None,'node_name':form['node_name'],'roles':form['roles'],'expected_revision':form['expected_revision'],'release_id':package['release_id'],'devices':devices,'operation_id':identity})
                node_id=credential['id'];self.update(identity,node_id=node_id)
                credential.update(api_url=form['api_url'],runner_image=secret['runner_image'],execution_modes=[form['mode']],pool_id=None,mock=False)
                self.update(identity,stage='TRANSFER')
                _,raw,_=remote.run(['mktemp','-d','/tmp/mini-eval-node.XXXXXXXX']);remote_dir=raw.decode().strip()
                if not re.fullmatch(r'/tmp/mini-eval-node\.[A-Za-z0-9]+',remote_dir):raise Rejected('invalid_remote_temporary_directory')
                try:
                    remote.upload(remote_dir+'/bundle.zip',payload);remote.upload(remote_dir+'/credential.json',json.dumps(credential).encode())
                    extract="import hashlib,sys,zipfile; p=sys.argv[1]; d=open(p+'/bundle.zip','rb').read(); assert hashlib.sha256(d).hexdigest()==sys.argv[2]; zipfile.ZipFile(p+'/bundle.zip').extractall(p)"
                    remote.run([plan['python'],'-c',extract,remote_dir,package['sha256']])
                    args=[plan['python'],'-B',remote_dir+'/scripts/install_node.py','--credential',remote_dir+'/credential.json']
                    if plan['existing']:
                        args+=['--existing','--home',plan['home']]
                        for s in plan['services']:args+=['--service',s['scope']+':'+s['name']]
                    else:args+=['--gpus',','.join(map(str,plan['selected_gpus']))]
                    if plan.get('resource_repair_required'):
                        self.update(identity,stage='RESOURCE_SETUP')
                        remote.run([plan['python'],remote_dir+'/scripts/configure_node_resources.py','--uid',str(plan['uid'])],sudo=True,timeout=60)
                        # Re-exec refreshes the manager's cached controllers without restarting services.
                        env=['env','XDG_RUNTIME_DIR=/run/user/'+str(plan['uid']),
                             'DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/'+str(plan['uid'])+'/bus']
                        remote.run(env+['systemctl','--user','daemon-reexec'],timeout=30)
                    if form['mode']=='host':
                        self.update(identity,stage='RESOURCE_VERIFY')
                        limits=self.host_resources(remote,form)
                        self.update(identity,resource_limits=limits)
                        if not limits['ready']:raise Rejected('runtime_limits_not_enforced')
                    self.update(identity,stage='INSTALL')
                    remote.run(args,timeout=420,sudo=bool(plan['services'] and plan['services'][0]['scope']=='system'))
                finally:
                    try:remote.run(['rm','-rf','--',remote_dir],timeout=20)
                    except Exception:self.update(identity,cleanup_warning=True)
            self.update(identity,'VERIFYING','RECONNECT')
            until=time.monotonic()+120
            while time.monotonic()<until:
                status=control.call('admin/nodes/'+node_id+'/enrollment')
                self.update(identity,reconnect=status)
                if status['ready']:
                    control.call('admin/nodes/'+node_id+'/enrollment/complete',{'expected_revision':status['revision']})
                    self.update(identity,'SUCCEEDED','COMPLETE');return
                if status['state']=='COMPLETE':self.update(identity,'SUCCEEDED','COMPLETE');return
                time.sleep(2)
            raise Rejected('component_reconnect_timeout')
        except Exception as error:self.fail(identity,error)
    def close(self):self.pool.shutdown(wait=True,cancel_futures=True);self.secrets.clear()
