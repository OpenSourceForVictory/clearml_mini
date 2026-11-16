"""Re-enroll a tombstoned identity; installation is outside the API process."""
import base64
import hashlib
import io
from pathlib import Path
import secrets
import zipfile
from datetime import timedelta
from fastapi import Depends, HTTPException
from sqlalchemy import select
from .core import Worker, Slot, now
from .administration import Revision, audit
from .scheduling import lock, utc, seconds
from .worker_upgrades import build_release, updater_online, BLOCKING


def pending(w):
    return (w.connection or {}).get('enrollment', {}).get('state') == 'INSTALLING'


def bundle():
    release = build_release()
    root = Path(__file__).resolve().parents[2]
    out = io.BytesIO()
    with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('worker-release.zip', release.archive)
        z.writestr('release.sha256', release.id)
        for name in ('install_node.py', 'deploy_node.py', 'install_worker.py', 'install_worker_updater.py', 'install_node_agent.py', 'configure_node_resources.py'):
            z.writestr('scripts/'+name, (root/'scripts'/name).read_bytes())
        with zipfile.ZipFile(io.BytesIO(release.archive)) as worker:
            for name in worker.namelist():
                z.writestr(name, worker.read(name))
    return out.getvalue(), release, hashlib.sha256((root/'worker/updater.py').read_bytes()).hexdigest()


def progress(s, w):
    record = (w.connection or {}).get('enrollment') or {}
    slots = list(s.scalars(select(Slot).where(Slot.worker_id == w.id)))
    checks = []
    for slot in slots:
        software = slot.software or {}
        fresh = bool(slot.last_seen_at and timedelta(0) <= now()-utc(slot.last_seen_at) < timedelta(seconds=seconds('WORKER_TTL_SECONDS',30)))
        ready = bool(fresh and software.get('build_digest') == record.get('release_id')
                     and slot.protocol_version == record.get('protocol_version')
                     and software.get('version') == record.get('version')
                     and set(software.get('capabilities',[])) == set(record.get('capabilities',[]))
                     and slot.last_seen_at and record.get('started_at')
                     and utc(slot.last_seen_at).isoformat() >= record['started_at'])
        checks.append({'id':slot.id, 'gpu_index':slot.gpu_index, 'ready':ready})
    agent = updater_online(w) and record.get('agent_seen_digest') == record.get('agent_digest') and bool(record.get('agent_digest'))
    identities = ({x['id'] for x in checks} == set(record['slot_ids'])) if record.get('slot_ids') else ({(x.gpu_index,x.gpu_uuid) for x in slots} == {(x['index'],x['uuid']) for x in record.get('expected_devices',[])})
    from .node_agents import record as agent_record, fresh
    node_agent=agent_record(w)
    node_agent_ready=bool(fresh(node_agent) and node_agent.get('digest')==record.get('release_id'))
    ready = bool((not record.get('node_agent_required') or node_agent_ready) and not w.revoked_at and pending(w) and checks and identities
                 and all(x['ready'] for x in checks) and agent)
    return {'state':record.get('state'), 'revision':w.management_revision,
            'slots':checks, 'updater_ready':bool(agent), 'node_agent_ready':node_agent_ready, 'ready':ready}


def begin(s,w,user,release,agent,revision,expected_devices=None,new=False):
    from .execution import token_hash
    if revision != w.management_revision: raise HTTPException(409,'machine configuration changed; refresh')
    if not new and not w.revoked_at and not pending(w): raise HTTPException(409,'unmanage the node before re-enrolling')
    if w.upgrade_state in BLOCKING: raise HTTPException(409,'resolve node upgrade before re-enrolling')
    devices=list(s.scalars(select(Slot).where(Slot.worker_id==w.id)))
    if not devices and not expected_devices:raise HTTPException(409,'no GPU installation plan; use SSH environment check')
    if devices and expected_devices is not None:
        actual={(x.gpu_index,x.gpu_uuid) for x in devices}
        if actual!={(x['index'],x['uuid']) for x in expected_devices}:raise HTTPException(409,'SSH target GPU identities differ from registered node')
    token='wk_'+secrets.token_urlsafe(32)
    w.token_hash=token_hash(token);w.revoked_at=None;w.enabled=False
    w.updater_seen_at=None;w.management_revision+=1
    if (w.connection or {}).get('agent'):
        w.connection={**w.connection,'agent':{**w.connection['agent'],'seen_at':None,'session':None,'boot_id':None,'config_status':'waiting'}}
    w.connection={**(w.connection or {}),'enrollment':{
        'state':'INSTALLING','started_at':now().isoformat(),'release_id':release.id,
        'protocol_version':release.protocol_version,'version':release.version,
        'capabilities':release.capabilities,'agent_digest':agent,'node_agent_required':True,
        'slot_ids':[x.id for x in devices], 'expected_devices':expected_devices or []}}
    for slot in devices:slot.enabled=False;slot.software=None
    audit(s,user,'machine.reenroll' if not new else 'machine.ssh-enroll',w.id)
    s.commit()
    return {'id':w.id,'node_name':w.node_name,'token':token,'revision':w.management_revision,'release_id':release.id}


def accepts_new_gpu(w,index,uuid):
    record=(w.connection or {}).get('enrollment',{})
    return pending(w) and not record.get('slot_ids') and {'index':index,'uuid':uuid} in record.get('expected_devices',[])


def install(app, db, admin):
    def machine(s, identity):
        w = s.get(Worker, identity)
        if not w: raise HTTPException(404, 'node not found')
        return w

    @app.get('/api/v1/admin/node-installation-bundle')
    def download(user=Depends(admin)):
        payload, release, agent = bundle()
        return {'filename':'mini-eval-node.zip', 'archive':base64.b64encode(payload).decode(),
                'sha256':hashlib.sha256(payload).hexdigest(), 'release_id':release.id, 'agent_digest':agent}

    @app.post('/api/v1/admin/nodes/{identity}/reenroll')
    def start(identity:str, body:Revision, user=Depends(admin), s=Depends(db)):
        _, release, agent = bundle()
        lock(s); w = machine(s, identity)
        return begin(s,w,user,release,agent,body.expected_revision)

    @app.get('/api/v1/admin/nodes/{identity}/enrollment')
    def status(identity:str, user=Depends(admin), s=Depends(db)):
        return progress(s, machine(s, identity))

    @app.post('/api/v1/admin/nodes/{identity}/enrollment/complete')
    def complete(identity:str, body:Revision, user=Depends(admin), s=Depends(db)):
        lock(s); w = machine(s, identity)
        if body.expected_revision != w.management_revision: raise HTTPException(409, 'machine configuration changed; refresh')
        if not progress(s, w)['ready']: raise HTTPException(409, 'all original GPU workers and the updater must reconnect with the installation bundle')
        w.connection = {**w.connection, 'enrollment':{**w.connection['enrollment'], 'state':'COMPLETE'}}
        w.enabled = True; w.management_revision += 1
        for slot in s.scalars(select(Slot).where(Slot.worker_id == w.id)): slot.enabled = True
        audit(s, user, 'machine.reenroll.complete', w.id); s.commit()
        return progress(s, w)
