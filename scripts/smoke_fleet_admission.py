"""Validate oversized requests without changing node capacities or stopping workloads."""
import argparse,json,time
from pathlib import Path
from scripts.fleet_release_api import API
from scripts.smoke_fleet_orchestration import Acceptance


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--token-file',type=Path,required=True);p.add_argument('--directory',type=Path,required=True);p.add_argument('--run-id',required=True)
    args=p.parse_args();api=API('http://127.0.0.1:18000',json.loads(args.token_file.read_text())['token'])
    test=Acceptance(api,args.directory,args.run_id)
    for resource in ('cpus','ram_gb'):
        body=test.body(command=True);body['resources']={resource:10000}
        try:test.submit('oversized-'+resource+'-'+str(time.time_ns()),body)
        except RuntimeError as e:
            if 'HTTP 400' not in str(e):raise
        else:raise AssertionError('request exceeds current pool limit but was admitted')
        test.registry.record('E05-'+resource,'passed',{'request':10000,'result':'rejected before scheduling'})
    body=test.body(command=True);body.update(gpu_mode='shared',requested_vram_gb=1000)
    identity=test.submit('oversized-vram-'+str(time.time_ns()),body)
    time.sleep(3)
    current=api('batches/'+identity+'/monitor')
    assert all(c['status']=='PENDING' for c in current['cases'])
    assert all(api('jobs/'+c['job_id'])['attempts'][-1]['slot_id'] is None for c in current['cases'])
    test.registry.record('E05-vram','passed',{'batch':identity,'result':'queued without allocating a device','monitor':current})
    test.registry.cancel_batch(api,identity,'admin')
    print('PASS CPU/RAM request rejection and VRAM exhaustion queueing without node configuration changes')

if __name__=='__main__':main()
