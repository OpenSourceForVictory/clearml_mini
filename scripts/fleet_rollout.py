"""Drain, back up, publish and upgrade the explicitly inventoried five-node fleet."""
import argparse
import json
from pathlib import Path
import re
import subprocess
from scripts.fleet_release import Checkpoints, atomic_json, probe, verify_identity, TARGETS
from scripts.fleet_release_api import API, drain, upgrade_nodes, restore_admission, assert_release_ready, reconcile_upgrade
from scripts.fleet_release_control import backup_database, restore_probe, compose_command


def run(argv):
    result=subprocess.run(argv,capture_output=True,text=True,timeout=300)
    if result.returncode:raise RuntimeError('release command failed; inspect local service logs without exposing credentials')
    return result.stdout


def image_overlay(path,image):
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]{0,499}',image):raise ValueError('invalid release image')
    path.write_text('services:\n'+''.join('  '+name+':\n    image: '+image+'\n' for name in ('api','migrate','bootstrap')))


def check_compose_environment(command,active):
    desired=json.loads(run(command+['config','--format','json']))['services']['api']
    current=dict(x.split('=',1) for x in active['Config']['Env'] if '=' in x)
    keys=('DATABASE_URL','JWT_SECRET','RUNNER_IMAGE','MAX_ACTIVE_GLOBAL','MAX_ACTIVE_PER_USER',
          'MAX_PENDING_PER_USER','MAX_BATCH_BACKLOG_PER_USER','LEASE_SECONDS','LOST_WORKER_GRACE_SECONDS','EXECUTION_TIMEOUT_SECONDS')
    if any(str(desired['environment'].get(k,''))!=current.get(k,'') for k in keys):
        raise RuntimeError('deployment environment changed; reconcile configuration before release')
    ports={(p['host_ip'],str(p['published']),str(p['target'])) for p in desired.get('ports',[])}
    previous={(p['HostIp'],p['HostPort'],key.split('/')[0]) for key,values in active['HostConfig']['PortBindings'].items() for p in values}
    if ports!=previous:raise RuntimeError('deployment listeners changed; refusing release')


def complete(journal, plan):
    steps=journal.state['steps']
    if any(steps.get(k,{}).get('status')!='done' for k in ('backup','control')):return False
    for target in plan['targets']:
        name=target['node_name'];drained=steps.get('drain:'+name,{})
        if drained.get('status')!='done' or steps.get('upgrade:'+name,{}).get('status')!='done':return False
        if drained.get('was_enabled') and not drained.get('restored'):return False
    return True


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('action',choices=['check','apply','resume','restore-admission'])
    p.add_argument('--plan',type=Path,required=True);p.add_argument('--token-file',type=Path,required=True)
    p.add_argument('--state-directory',type=Path,required=True);p.add_argument('--release-id',required=True)
    p.add_argument('--image',required=True);p.add_argument('--env-file',type=Path,default=Path('.env'))
    p.add_argument('--reconcile-node');p.add_argument('--api',default='http://127.0.0.1:18000')
    args=p.parse_args();plan=json.loads(args.plan.read_text())
    if len(plan['targets'])!=5 or {t['address']:t['node_name'] for t in plan['targets']}!=TARGETS:
        p.error('release plan must contain exactly the approved five nodes')
    api=API(args.api,json.loads(args.token_file.read_text())['token'])
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]{0,499}',args.image):p.error('invalid image')
    with Checkpoints(args.state_directory,args.release_id).locked() as journal:
        for target in plan['targets']:verify_identity(target,probe(target['address']))
        if args.action=='restore-admission':
            # Explicit recovery only; callers must establish component compatibility first.
            release=json.loads((args.state_directory/'published-release.json').read_text())
            assert_release_ready(api,plan,release['id'])
            restore_admission(api,plan,journal);return
        image_id=json.loads(run(['docker','image','inspect',args.image]))[0]['Id']
        active=json.loads(run(['docker','inspect','mini-eval-api-1']))[0]
        if active['Config']['Labels'].get('com.docker.compose.project')!='mini-eval':raise RuntimeError('unexpected control-plane container identity')
        overlay=args.state_directory/'image.yml';image_overlay(overlay,args.image)
        command=compose_command(args.env_file,overlay)
        check_compose_environment(command,active)
        if args.action=='check':
            print('PASS physical identities, mounts, release image, environment and listeners; no platform mutation')
            return
        if journal.begin('identity',{'image_id':image_id,'plan':plan}):journal.finish('identity')
        previous=args.state_directory/'previous-deployment.json'
        if not previous.exists():
            atomic_json(previous,{'image':active['Config']['Image'],'image_id':active['Image'],
                                 'compose_files':active['Config']['Labels'].get('com.docker.compose.project.config_files'),
                                 'ports':active['HostConfig']['PortBindings']})
        published=args.state_directory/'published-release.json'
        if complete(journal,plan) and published.exists():
            assert_release_ready(api,plan,json.loads(published.read_text())['id'])
            print('PASS release already complete; no operations repeated');return
        if args.reconcile_node:
            if args.action!='resume' or not published.exists():raise ValueError('reconciliation requires an existing release and resume')
            reconcile_upgrade(api,plan,journal,args.reconcile_node,json.loads(published.read_text())['id'])
        drain(api,plan,journal,timeout=300)
        print('PASS drained',flush=True)
        if journal.begin('backup',{'image_id':image_id}):
            backup=args.state_directory/'release.dump';digest=backup_database(backup)
            atomic_json(args.state_directory/'release-restore.json',restore_probe(backup,digest,args.image))
            journal.finish('backup')
        if journal.begin('control',{'image_id':image_id}):
            run(command+['stop','api'])
            run(command+['run','--rm','--no-deps','migrate'])
            run(command+['up','-d','--no-deps','--wait','api'])
            journal.finish('control')
        release=api('admin/worker-releases',{},'POST');atomic_json(published,release)
        upgrade_nodes(api,plan,journal,release['id'])
        assert_release_ready(api,plan,release['id']);restore_admission(api,plan,journal)
        print('PASS five-node release completed',flush=True)


if __name__=='__main__':main()
