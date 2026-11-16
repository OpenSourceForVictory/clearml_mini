"""Host backend with stable systemd unit identity, never a PID-only handle."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import uuid
from .host_environments import probe,user_environment
from .storage import atomic_json
from .workspace import execution_dirs,validate_storage,disk_status
from runner.template_contract import host_config
from runner.image_runtime import TOKEN
from runner.cs_contract import MAX_ARTIFACT_BYTES,MAX_ARTIFACT_COUNT,MAX_ARTIFACT_TOTAL,artifact_name


class HostBackend:
    def __init__(self,worker):
        self.worker=worker
        self.system=worker.config.get('accelerator_type')=='npu' or bool(worker.config.get('host_execution')) or os.geteuid()==0
        self.execution=worker.state['task']['execution_id'] if worker.state else None
        legacy=Path(str(worker.path)+'.host')
        # Business UIDs must never traverse the private Worker journal directory.
        # Existing handles remain recoverable across this layout change.
        self.root=(Path(worker.workspace)/'runs'/str(uuid.UUID(self.execution))/'host-control'
                   if self.system and self.execution and not legacy.exists() else legacy)
    def unit(self,execution):return 'mini-eval-exec-'+str(uuid.UUID(execution))+'.service'
    def control(self,*args,check=True):
        p=subprocess.run(['systemctl',*([] if self.system else ['--user']),*args],env=user_environment(),capture_output=True,text=True,timeout=15)
        if check and p.returncode:raise RuntimeError('host unit control failed: '+p.stderr[-500:])
        return p
    def inspect(self,execution):
        p=self.control('show',self.unit(execution),'--property=LoadState,Description,ActiveState,SubState,ExecMainStatus',check=False)
        values=dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)
        if values.get('LoadState')=='not-found':
            if (self.root/'launch.json').exists():
                launch=json.loads((self.root/'launch.json').read_text())
                if launch['execution_id']!=execution:raise RuntimeError('host execution identity mismatch')
                if not launch.get('started'):return {'State':{'Status':'created','Running':False,'ExitCode':0}}
            return None
        if p.returncode:raise RuntimeError('cannot inspect host unit: '+p.stderr[-500:])
        if values.get('Description')!='Mini Eval execution '+execution:raise RuntimeError('host unit identity mismatch')
        running=values.get('SubState') in ('running','start','start-pre','start-post','stop','stop-sigterm','stop-sigkill') or values.get('ActiveState')=='activating'
        code=int(values.get('ExecMainStatus','1'))
        if (self.root/'exit.json').exists():code=json.loads((self.root/'exit.json').read_text())['code']
        return {'State':{'Status':'running' if running else 'exited','Running':running,'ExitCode':code}}
    def stop(self,execution):
        item=self.inspect(execution)
        if item and item['State']['Status']!='created':
            self.control('stop',self.unit(execution))
            item=self.inspect(execution)
            if item and item['State']['Running']:raise RuntimeError('host execution has not stopped')
    def remove(self,execution):
        self.stop(execution);self.control('reset-failed',self.unit(execution),check=False)
        # Keep bounded output for diagnostics, remove launch marker to prevent reuse.
        (self.root/'launch.json').unlink(missing_ok=True)
    def create(self,task):
        w=self.worker
        npu=None
        account=None
        if task.get('accelerator_type')=='npu':
            from .accelerators import assignment
            from .npu_host import settings
            assignment(w.config,task);npu=settings(w.config);account=npu[0]
        elif w.config.get('accelerator_type')=='npu':raise RuntimeError('GPU task cannot run on NPU Worker')
        elif self.system:
            from .host_identity import settings
            account=settings(w.config)
        approved=w.state.get('host_approval',{})
        host=host_config(task['client_spec']['template']['host_runtime'])
        profile=approved.get('environments',{}).get(host['environment_name'])
        if approved.get('revision')!=task.get('host_revision') or profile!=task.get('host_profile'):raise RuntimeError('host environment approval changed or is missing')
        probe(profile,w.config)
        validate_storage(w.config,w.config.get('_supervisor_state',w.path),w.config.get('_config_path'))
        if task.get('storage_revision',0)!=w.storage_snapshot['revision']:raise RuntimeError('storage revision mismatch')
        disk=disk_status(w.workspace,w.config)
        if disk['free_mb']<disk['min_free_mb']:raise RuntimeError('insufficient workspace disk')
        if task.get('requested_vram_mb'):
            from .client import telemetry
            if telemetry(w.config)['free_vram_mb']<task['requested_vram_mb']+task.get('vram_reserve_mb',0):raise RuntimeError('insufficient free GPU memory')
        directory=execution_dirs(w.workspace,task['execution_id'])
        if self.root.is_symlink():raise RuntimeError('host control directory must not be a symlink')
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        inp=directory/'input.json'
        config={**task['client_spec'],'job_id':task['job_id'],'execution_id':task['execution_id'],'episodes':task['episodes'],'timeout_seconds':task['timeout_seconds']}
        values={k:str(config.get(k,'')) for k in ('task_name','episodes','seed','endpoint')}
        if config.get('workload') == 'service':
            config.update(port=config['service']['port'], bind_host=config['service']['address'],health_port=config['service'].get('health_port',config['service']['port']))
            values.update(port=str(config['port']), bind_host=config['bind_host'],health_port=str(config['health_port']))
        values.update(input_path=str(inp),work_dir=str(directory/'work'),output_dir=str(directory/'output'))
        assets=w.runtime_snapshot['config']['assets']
        for name in host['assets']:
            if name not in assets:raise RuntimeError('unconfigured logical asset: '+name)
            validate_storage({**w.config,'assets_root':assets[name],'cache_root':assets[name]},w.config.get('_supervisor_state',w.path),w.config.get('_config_path'))
            values['assets:'+name]=assets[name]
        def expand(text):return TOKEN.sub(lambda m:values.get(m[1],m[0]),text)
        argv=[expand(x) for x in config['template']['command']]
        code_root=Path(profile['code_root']).resolve()
        business_cwd=directory/'work'
        if host.get('working_directory') is not None:
            business_cwd=(code_root/host['working_directory']).resolve()
            if not business_cwd.is_relative_to(code_root) or not business_cwd.is_dir() or not os.access(business_cwd,os.R_OK|os.X_OK):
                raise RuntimeError('host working_directory must be an accessible directory inside approved code root')
        def code_path(text):
            base=business_cwd if host.get('working_directory') is not None else code_root
            p=(base/text).resolve()
            if not p.is_relative_to(code_root) or not p.is_file():raise RuntimeError('business program must exist inside approved code root')
            return str(p)
        if argv[0] in ('python','python3',profile['python']):
            argv[0]=profile['python']
            if len(argv)>1 and not argv[1].startswith('-'):argv[1]=code_path(argv[1])
        else:
            argv[0]=code_path(argv[0])
            if not os.access(argv[0],os.X_OK):raise RuntimeError('business program is not executable')
        platform_root=str(Path(__file__).resolve().parent.parent)
        env={'PATH':str(Path(profile['python']).parent)+':/usr/local/bin:/usr/bin:/bin','HOME':str(Path.home()),'LANG':'C.UTF-8','PYTHONUNBUFFERED':'1','PYTHONPATH':platform_root+':'+str(code_root),
             'CUDA_VISIBLE_DEVICES':task.get('gpu_uuid') or '', 'MINI_EVAL_INPUT':str(inp),'MINI_EVAL_WORK_DIR':values['work_dir'],'MINI_EVAL_OUTPUT_DIR':values['output_dir'],
             'MINI_EVAL_ASSETS_DIR':w.config['assets_root'],'MINI_EVAL_CACHE_DIR':w.config['cache_root']}
        env.update({k:expand(v) for k,v in {**host['environment'],**config.get('environment',{})}.items()})
        if account:env['HOME']=account.pw_dir
        if npu:
            account,device,cann_env=npu
            env.update(cann_env)
            env['HOME']=account.pw_dir
            env['PYTHONPATH']=platform_root+':'+str(code_root)+(':'+cann_env['PYTHONPATH'] if cann_env.get('PYTHONPATH') else '')
            env.pop('CUDA_VISIBLE_DEVICES',None)
            # The device policy leaves one accessible card; CANN enumerates it as logical zero.
            env['ASCEND_RT_VISIBLE_DEVICES']='0'
        config['_resolved_cwd']=str(business_cwd)
        config['_resolved_command']=argv;config['_resolved_environment']=env;config['_resolved_assets']={k:v for k,v in values.items() if k.startswith('assets:')}
        config['template']={**config['template'],'command':argv}
        atomic_json(inp,config)
        module='runner.service_entry' if config.get('workload') == 'service' else 'runner.cs_entry'
        launch={'resource_limits':{'cpus':task['cpus'],'memory_bytes':task['ram_gb']*1024**3},'execution_id':task['execution_id'],'started':False,'command':[profile['python'],'-m',module],'environment':env,'cwd':values['work_dir']}
        if npu:launch['npu_device']=npu[1]
        atomic_json(self.root/'launch.json',launch)
        if account:
            for base in (self.root,directory):
                for item in [base,*base.rglob('*')]:
                    if item.is_symlink():raise RuntimeError('unexpected symlink in new execution directory')
                    os.chown(item,account.pw_uid,account.pw_gid)
                    if item.is_dir():item.chmod(0o700)
            # Only platform execution containers get traversal; never chmod arbitrary code/assets.
            for parent in (Path(w.workspace),Path(w.workspace)/'runs'):
                if parent.is_symlink():raise RuntimeError('workspace ancestor must not be a symlink')
                owner=parent.stat()
                if owner.st_uid==0:parent.chmod(owner.st_mode | 0o011)
            from .host_identity import check_paths
            paths=[('Python',profile['python'],os.R_OK|os.X_OK),('code root',str(code_root),os.R_OK|os.X_OK),
                   ('business cwd',str(business_cwd),os.R_OK|os.X_OK),('platform code',platform_root,os.R_OK|os.X_OK),
                   ('work directory',values['work_dir'],os.R_OK|os.W_OK|os.X_OK),('output directory',values['output_dir'],os.R_OK|os.W_OK|os.X_OK),
                   ('cache directory',w.config['cache_root'],os.R_OK|os.W_OK|os.X_OK)]
            paths += [('asset '+name,assets[name],os.R_OK|os.X_OK) for name in host['assets']]
            if len(argv)>1 and argv[0]==profile['python'] and not argv[1].startswith('-'):paths.append(('business script',argv[1],os.R_OK))
            check_paths(account,paths)
    def start(self,task):
        path=self.root/'launch.json';launch=json.loads(path.read_text())
        if launch['started']:raise RuntimeError('refusing duplicate host launch')
        owner=path.stat()
        launch['started']=True;atomic_json(path,launch)
        if self.system:os.chown(path,owner.st_uid,owner.st_gid)
        properties={'Description':'Mini Eval execution '+task['execution_id'],'CPUQuota':str(task['cpus']*100)+'%','MemoryMax':str(task['ram_gb'])+'G','TasksMax':'256','KillMode':'control-group','RemainAfterExit':'yes','TimeoutStopSec':'10','RuntimeMaxSec':str(task['timeout_seconds']),'StandardOutput':'null','StandardError':'append:'+str((self.root/'supervisor.log').resolve())}
        command=['systemd-run',*([] if self.system else ['--user']),'--quiet','--unit='+self.unit(task['execution_id']),'--working-directory='+str(Path(__file__).resolve().parent.parent)]
        for k,v in properties.items():command+=['--property='+k+'='+v]
        if self.worker.config.get('accelerator_type')=='npu':
            from .npu_host import properties as npu_properties
            command+=['--property='+value for value in npu_properties(self.worker.config)]
        elif self.system:
            from .host_identity import settings,properties as identity_properties
            command+=['--property='+value for value in identity_properties(settings(self.worker.config))]
        command+=['/usr/bin/python3','-m','worker.host_supervisor',str(path.resolve())]
        p=subprocess.run(command,env=user_environment(),capture_output=True,text=True,timeout=15)
        if p.returncode:raise RuntimeError('host launch failed: '+p.stderr[-500:])
        try:
            group=self.control('show',self.unit(task['execution_id']),'--property=ControlGroup','--value').stdout.strip()
            from .resource_limits import verify_group
            verify_group(group,task['cpus'],task['ram_gb']*1024**3)
        except Exception:
            self.stop(task['execution_id'])
            raise

    def logs(self):
        def read(name):
            p=self.root/name
            return p.read_bytes()[:262144].decode(errors='replace') if p.exists() else ''
        out=read('stdout.log')
        if (self.root/'final.json').exists():out+='\n'+read('final.json')+'\n'
        return out,read('stderr.log')+read('supervisor.log')
    def artifacts(self,execution):
        root=execution_dirs(self.worker.workspace,execution)/'output';files=[];total=0
        for i,p in enumerate(root.rglob('*')):
            if i>128:raise RuntimeError('too many artifact entries')
            name=p.relative_to(root).as_posix()
            if p.is_symlink() or not artifact_name(name):raise RuntimeError('invalid artifact path')
            if p.is_dir():continue
            if not p.is_file():raise RuntimeError('artifact is not a regular file')
            with p.open('rb') as f:data=f.read(MAX_ARTIFACT_BYTES+1)
            total+=len(data)
            if len(data)>MAX_ARTIFACT_BYTES or len(files)>=MAX_ARTIFACT_COUNT or total>MAX_ARTIFACT_TOTAL:raise RuntimeError('artifact quota exceeded')
            files.append((name,data))
        return files
