"""Read-only bounded fleet observation. Credentials are read from environment only."""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import urllib.request


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--api-url',required=True)
    p.add_argument('--username',default='admin')
    p.add_argument('--password-env',default='MINI_EVAL_ADMIN_PASSWORD')
    p.add_argument('--duration',type=int,default=600)
    p.add_argument('--interval',type=int,default=5)
    p.add_argument('--node',action='append',required=True,help='Expected node name; repeat for each node')
    p.add_argument('--output',type=Path,required=True)
    a=p.parse_args()
    if not 1<=a.duration<=86400 or not 1<=a.interval<=60:p.error('duration 1..86400, interval 1..60 seconds')
    base=a.api_url.rstrip('/')+'/api/v1/'
    def request(path,body=None,token=None):
        req=urllib.request.Request(base+path,data=json.dumps(body).encode() if body is not None else None,
            headers={'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {})})
        with urllib.request.urlopen(req,timeout=10) as r:return json.load(r)
    token=request('auth/login',{'username':a.username,'password':os.environ[a.password_env]})['access_token']
    a.output.parent.mkdir(parents=True,exist_ok=True)
    start=time.monotonic();last_login=start;counts={name:{'samples':0,'offline':0,'unapplied':0,'max_heartbeat_age':0.0,'max_event_count':0} for name in a.node}
    errors=0
    with a.output.open('x') as stream:
        while True:
            elapsed=time.monotonic()-start
            row={'elapsed_seconds':round(elapsed,2),'observed_at':datetime.now(timezone.utc).isoformat(),'nodes':{}}
            try:
                if time.monotonic()-last_login>=1200:
                    token=request('auth/login',{'username':a.username,'password':os.environ[a.password_env]})['access_token']
                    last_login=time.monotonic()
                nodes={n['node_name']:n for n in request('admin/nodes',token=token)}
                for name,summary in counts.items():
                    n=nodes.get(name,{});agent=(n.get('connection') or {}).get('agent',{})
                    value={k:agent.get(k) for k in ('status','sequence','revision','applied_revision','config_status','version','digest','seen_at','programs')}
                    value.update(worker_status=n.get('status'),held_executions=n.get('held_executions'))
                    age=None
                    if agent.get('seen_at'):
                        age=max(0,(datetime.now(timezone.utc)-datetime.fromisoformat(agent['seen_at'])).total_seconds())
                        summary['max_heartbeat_age']=max(summary['max_heartbeat_age'],age)
                    value['heartbeat_age_seconds']=age
                    summary['samples']+=1
                    summary['offline']+=agent.get('status')!='online'
                    summary['unapplied']+=agent.get('config_status')!='applied' or agent.get('revision')!=agent.get('applied_revision')
                    summary['max_event_count']=max(summary['max_event_count'],len(agent.get('events',[])))
                    row['nodes'][name]=value
            except Exception as exc:
                errors+=1;row['error']=type(exc).__name__  # no raw responses or credentials
            stream.write(json.dumps(row)+'\n');stream.flush()
            if elapsed>=a.duration:break
            time.sleep(min(a.interval,a.duration-elapsed))
    result={'duration_seconds':round(time.monotonic()-start,2),'api_errors':errors,'nodes':counts,
        'limitation':'Short observation only; injected faults appear as offline/unapplied samples.'}
    a.output.with_suffix('.summary.json').write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))


if __name__=='__main__':main()
