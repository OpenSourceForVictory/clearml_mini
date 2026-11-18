import json,os,subprocess,sys,uuid
from pathlib import Path
root=Path('.runtime/bounded-logs-benchmark').resolve();rows={}
for version in (1,2):
 path=root/('v'+str(version)+'-'+uuid.uuid4().hex);path.mkdir()
 program="import sys,json;sys.stdout.write('x'*16777216+'\\n');sys.stdout.flush();sys.stderr.write('LAST-ERROR\\n');print(json.dumps({'final':{'success_rate':1}}))"
 config={'command':[sys.executable,'-c',program],'environment':dict(os.environ),'cwd':str(path),'log_protocol':version}
 launch=path/'launch.json';launch.write_text(json.dumps(config));launch.chmod(0o600)
 usage=path/'usage.json'
 subprocess.run(['/usr/bin/time','-f','{"max_rss_kib":%M,"elapsed_seconds":%e,"user_seconds":%U,"system_seconds":%S}','-o',str(usage),sys.executable,'-m','worker.host_supervisor',str(launch)],check=True)
 rows[str(version)]={**json.loads(usage.read_text()),'diagnostic_bytes':sum(p.stat().st_size for p in (path/'logs').glob('*.json')) if version==2 else sum((path/(name+'.log')).stat().st_size for name in ('stdout','stderr')),'input_stdout_bytes':16777217}
 assert rows[str(version)]['diagnostic_bytes']<4*1024*1024
(root/'summary.json').write_text(json.dumps(rows,indent=2));print(json.dumps(rows))
