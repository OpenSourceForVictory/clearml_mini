"""Read-only environment probe, sent over SSH stdin; prints no credentials."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.request


def collect(options):
    os.environ.setdefault('XDG_RUNTIME_DIR',f'/run/user/{os.getuid()}')
    os.environ.setdefault('DBUS_SESSION_BUS_ADDRESS','unix:path='+os.environ['XDG_RUNTIME_DIR']+'/bus')
    home=Path.home()
    managed=Path('/var/lib/mini-eval/control')
    if os.geteuid()==0 and (managed/'.config/mini-eval').exists():home=managed
    configdir=home/'.config/mini-eval';configs=[];services=[];issues=[];installed_identity={}
    def run(argv):
        try:
            p=subprocess.run(argv,capture_output=True,text=True,timeout=15)
            return p.returncode,p.stdout
        except (OSError,subprocess.SubprocessError):return 1,''
    installation=None
    paths=sorted(configdir.glob('*.json'))
    if os.geteuid()==0 and options.get('layout_source'):
        scope={};exec(options['layout_source'],scope)
        try:
            installation=scope['discover'](options['node_name'],sys.executable)
            if installation:
                discovered=[Path(x) for x in installation['layout']['config_paths']]
                if paths and set(paths)!=set(discovered):raise ValueError('existing_layout_incomplete')
                paths=discovered
        except (ValueError,KeyError,TypeError,OSError,subprocess.SubprocessError) as error:
            code=str(error)
            allowed={'existing_layout_untrusted','existing_layout_incomplete','existing_layout_services_mismatch','installed_node_identity_conflict','execution_journal_pending','upgrade_journal_pending'}
            issues.append(code if code in allowed else 'existing_layout_invalid')
            installation=None
    for p in paths:
        v=json.loads(p.read_text())
        if not isinstance(v,dict) or 'state_path' not in v:continue
        configs.append({'path':str(p),'node_name':v.get('node_name'),'gpu_index':v.get('gpu_index',0),'mock':v.get('mock',False),'gpu_uuid':('mock:'+v.get('node_name','')+':'+str(v.get('gpu_index',0))) if v.get('mock') else None,
                        'workspace_root':v.get('workspace_root'),'assets_root':v.get('assets_root'),'cache_root':v.get('cache_root'),
                        'npu_sharing':v.get('npu_runtime',{}).get('allow_shared') is True if v.get('accelerator_type')=='npu' else None,
                        'gpu_sharing':v.get('gpu_runtime',{}).get('allow_shared') is True if v.get('accelerator_type','gpu')=='gpu' and isinstance(v.get('gpu_runtime'),dict) else None,
                        'execution_modes':v.get('execution_modes',['docker','host'])})
        installed_identity=v.get('host_execution') or {'user':v.get('npu_runtime',{}).get('host',{}).get('user')}
        if v.get('accelerator_type','gpu')!=options.get('accelerator_type','gpu'):issues.append('accelerator_type_conflict')
        state=Path(v['state_path'])
        if any(x.exists() and json.loads(x.read_text()) for x in [state,*Path(str(state)+'.runs').glob('*.json')]):issues.append('execution_journal_pending')
    for base,scope in ((Path('/etc/systemd/system'),'system'),(home/'.config/systemd/user','user')):
        for unit in base.glob('mini-eval-worker*.service'):
            if '@.' in unit.name:continue
            content=unit.read_text()
            if any(c['path'] in content for c in configs):services.append({'scope':scope,'name':unit.name})
    # Resolve instances of the standard user template without writing any unit.
    for c in configs:
        name='mini-eval-worker@'+Path(c['path']).stem+'.service'
        code,text=run(['systemctl','--user','show',name,'-p','ExecStart','--value'])
        if code==0 and c['path'] in text and not any(s['scope']=='system' for s in services):services.append({'scope':'user','name':name})
    services=list({(x['scope'],x['name']):x for x in services}.values())
    if installation:services=installation['layout']['services']
    if not configs and (home/'.local/share/mini-eval-worker').exists():issues.append('incomplete_installation_requires_recovery')
    if configs and (not services or len({s['scope'] for s in services})!=1):issues.append('original_services_ambiguous')
    if configs and any(c['node_name']!=options['node_name'] for c in configs):issues.append('installed_node_identity_conflict')
    if configs and not options.get('node_id'):issues.append('existing_installation_requires_reenrollment')
    kind=options.get('accelerator_type','gpu')
    gpus=[]
    if configs and all(c['mock'] for c in configs):
        gpus=[{'index':c['gpu_index'],'name':'virtual','uuid':c['gpu_uuid']} for c in configs]
    elif kind=='npu':
        if os.geteuid()!=0:issues.append('npu_onboarding_requires_root')
        try:
            scope={};exec(options['ascend_source'],scope)
            c,mapping=run(['npu-smi','info','-m']);c2,raw=run(['npu-smi','info'])
            if c or c2:raise ValueError()
            maps=scope['mappings'](mapping);values=scope['metrics'](raw,maps)
            for index,_ in maps.values():
                prefix='npu_'+str(index)+'_'
                if values[prefix+'health']!='OK':issues.append('npu_device_unhealthy')
                gpus.append({'index':index,'name':values[prefix+'model'],'uuid':values[prefix+'identity']})
        except (ValueError,KeyError):issues.append('npu_driver_unavailable')
    else:
        code,text=run(['nvidia-smi','--query-gpu=index,name,uuid','--format=csv,noheader,nounits'])
        if code:issues.append('nvidia_driver_unavailable')
        else:
            for line in text.splitlines():
                parts=[x.strip() for x in line.split(',')]
                if len(parts)>=3:gpus.append({'index':int(parts[0]),'name':parts[1],'uuid':parts[2]})
    chosen=sorted(x['index'] for x in gpus) if options['gpus']=='all' else [int(x) for x in options['gpus'].split(',')]
    if not chosen or not set(chosen)<={x['index'] for x in gpus}:issues.append('GPU_selection_unavailable')
    if configs and sorted(c['gpu_index'] for c in configs)!=sorted(chosen):issues.append('all_installed_GPUs_required')
    if sys.version_info<(3,10):issues.append('python_3_10_required')
    if not shutil.which('systemctl'):issues.append('systemd_required')
    if os.geteuid()!=0 and (not services or services[0]['scope']=='user'):
        if run(['systemctl','--user','show-environment'])[0]:issues.append('systemd_user_session_unavailable')
    if not configs and os.geteuid()!=0:issues.append('new_node_requires_root')
    if os.geteuid()==0 and not configs and (options.get('node_id') or Path('/etc/systemd/system/mini-eval-agent.service').exists()):issues.append('existing_installation_requires_identity_migration')
    if os.geteuid()==0 and not configs:
        import pwd
        requested=options.get('business_user') or 'root'
        uid=options.get('business_uid',0 if requested=='root' else 29000);gid=options.get('business_gid',0 if requested=='root' else 29000)
        try:
            if pwd.getpwnam(requested).pw_uid != uid or pwd.getpwnam(requested).pw_gid != gid: issues.append('business_identity_conflict')
        except KeyError:
            issues.append('business_identity_conflict')
    workspace_paths=[]
    if configs:
        workspace_paths=[{'index':c['gpu_index'],'workspace_root':c['workspace_root'],
            'assets_root':c['assets_root'] or str(Path(c['workspace_root'])/'assets') if c['workspace_root'] else None,
            'cache_root':c['cache_root'] or str(Path(c['workspace_root'])/'cache') if c['workspace_root'] else None}
            for c in configs]
    else:
        base=Path(options.get('workspace_root') or '/var/lib/mini-eval/workspaces')
        forbidden=('/proc','/sys','/dev','/run','/etc','/boot','/root')
        if (not base.is_absolute() or str(base)=='/' or '..' in base.parts or ',' in str(base)
                or any(ord(c)<32 or ord(c)==127 for c in str(base))
                or any(base==Path(p) or Path(p) in base.parents for p in forbidden)
                or any(part.is_symlink() for part in (base,*base.parents))):
            issues.append('invalid_workspace_root')
        else:
            nearest=next((part for part in (base,*base.parents) if part.exists()),None)
            if nearest and not os.access(nearest,os.W_OK|os.X_OK):issues.append('workspace_root_not_writable')
            for index in chosen:
                path=base/options['node_name']/(kind+str(index))
                workspace_paths.append({'index':index,'workspace_root':str(path),
                    'assets_root':str(path/'assets'),'cache_root':str(path/'cache')})
    runner_ready=True
    docker_selected=options['mode'] in ('docker','both') or (options['mode']=='preserve' and any('docker' in c['execution_modes'] for c in configs))
    if docker_selected:
        if run(['docker','info'])[0]:issues.append('docker_unavailable')
        elif run(['docker','image','inspect',options['runner_image']])[0]:runner_ready=False
    try:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*a,**kw):return None
        with urllib.request.build_opener(NoRedirect()).open(options['api_url'].rstrip('/')+'/healthz',timeout=10) as response:
            if response.status!=200:issues.append('node_cannot_reach_API')
    except Exception:issues.append('node_cannot_reach_API')
    return {'installation':installation,'runner_image_ready':runner_ready,'cgroup_mode':'v2' if Path('/sys/fs/cgroup/cgroup.controllers').exists() else 'v1-or-hybrid','uid':os.getuid(),'accelerator_type':kind,'business_user':installed_identity.get('user') if configs else options.get('business_user'),'business_uid':installed_identity.get('uid') if configs else options.get('business_uid',0 if options.get('business_user')=='root' else 29000),'business_gid':installed_identity.get('gid') if configs else options.get('business_gid',0 if options.get('business_user')=='root' else 29000),'workspace_paths':workspace_paths,'home':str(home),'python':sys.executable,'python_version':sys.version.split()[0],
            'gpus':gpus,'selected_gpus':chosen,'existing':bool(configs),'services':services,
            'configs':configs,'issues':sorted(set(issues)),'free_disk_gb':round(shutil.disk_usage(home).free/1024**3,1)}

if __name__=='__main__':print(json.dumps(collect(json.loads(sys.stdin.readline()))))
