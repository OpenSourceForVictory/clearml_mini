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
    home=Path.home();configdir=home/'.config/mini-eval';configs=[];services=[];issues=[]
    def run(argv):
        try:
            p=subprocess.run(argv,capture_output=True,text=True,timeout=15)
            return p.returncode,p.stdout
        except (OSError,subprocess.SubprocessError):return 1,''
    for p in sorted(configdir.glob('*.json')):
        v=json.loads(p.read_text())
        if not isinstance(v,dict) or 'state_path' not in v:continue
        configs.append({'path':str(p),'node_name':v.get('node_name'),'gpu_index':v.get('gpu_index',0),'mock':v.get('mock',False),'gpu_uuid':('mock:'+v.get('node_name','')+':'+str(v.get('gpu_index',0))) if v.get('mock') else None})
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
    if not configs and (home/'.local/share/mini-eval-worker').exists():issues.append('incomplete_installation_requires_recovery')
    if configs and (not services or len({s['scope'] for s in services})!=1):issues.append('original_services_ambiguous')
    if configs and any(c['node_name']!=options['node_name'] for c in configs):issues.append('installed_node_identity_conflict')
    if configs and not options.get('node_id'):issues.append('existing_installation_requires_reenrollment')
    code,text=run(['nvidia-smi','--query-gpu=index,name,uuid','--format=csv,noheader,nounits'])
    gpus=[]
    if configs and all(c['mock'] for c in configs):
        gpus=[{'index':c['gpu_index'],'name':'virtual','uuid':c['gpu_uuid']} for c in configs]
    elif code==0:
        for line in text.splitlines():
            parts=[x.strip() for x in line.split(',')]
            if len(parts)>=3:gpus.append({'index':int(parts[0]),'name':parts[1],'uuid':parts[2]})
    elif configs and all(c['mock'] for c in configs):
        gpus=[{'index':c['gpu_index'],'name':'virtual','uuid':'mock'} for c in configs]
    else:issues.append('nvidia_driver_unavailable')
    chosen=sorted(x['index'] for x in gpus) if options['gpus']=='all' else [int(x) for x in options['gpus'].split(',')]
    if not chosen or not set(chosen)<={x['index'] for x in gpus}:issues.append('GPU_selection_unavailable')
    if configs and sorted(c['gpu_index'] for c in configs)!=sorted(chosen):issues.append('all_installed_GPUs_required')
    if sys.version_info<(3,10):issues.append('python_3_10_required')
    if not shutil.which('systemctl'):issues.append('systemd_required')
    if not services or services[0]['scope']=='user':
        if run(['systemctl','--user','show-environment'])[0]:issues.append('systemd_user_session_unavailable')
    if options['mode']=='docker':
        if run(['docker','info'])[0]:issues.append('docker_unavailable')
        elif run(['docker','image','inspect',options['runner_image']])[0]:issues.append('runner_image_missing')
    try:
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*a,**kw):return None
        with urllib.request.build_opener(NoRedirect()).open(options['api_url'].rstrip('/')+'/healthz',timeout=10) as response:
            if response.status!=200:issues.append('node_cannot_reach_API')
    except Exception:issues.append('node_cannot_reach_API')
    return {'uid':os.getuid(),'home':str(home),'python':sys.executable,'python_version':sys.version.split()[0],
            'gpus':gpus,'selected_gpus':chosen,'existing':bool(configs),'services':services,
            'configs':configs,'issues':sorted(set(issues)),'free_disk_gb':round(shutil.disk_usage(home).free/1024**3,1)}

if __name__=='__main__':print(json.dumps(collect(json.loads(sys.stdin.readline()))))
