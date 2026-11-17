"""One node-wide admission barrier and immutable release artifacts."""
import hashlib
import io
from pathlib import Path
import zipfile
from datetime import timedelta
from typing import Literal,Annotated
from fastapi import Depends,HTTPException,Response
from pydantic import BaseModel,ConfigDict,Field
from sqlalchemy import select
from .core import Worker,Slot,WorkerRelease,WorkerUpgrade,now
from .scheduling import lock,online,utc
from .storage import held
from .administration import audit,release_reservations

BLOCKING={'DRAINING','APPLYING','VERIFYING','ROLLING_BACK','FAILED'}


class Software(BaseModel):
    model_config=ConfigDict(extra='forbid')
    probe_references:list[Annotated[str,Field(pattern=r'^[A-Z_][A-Z0-9_]{0,99}$')]]=Field(default_factory=list,max_length=32)
    version:str=Field(min_length=1,max_length=100,pattern=r'^[A-Za-z0-9._+-]+$')
    build_digest:str|None=Field(default=None,pattern=r'^[a-f0-9]{64}$')
    capabilities:list[Annotated[str,Field(min_length=1,max_length=64,pattern=r'^[a-z][a-z0-9_]*$')]]=Field(default_factory=list,max_length=32)


class Start(BaseModel):
    model_config=ConfigDict(extra='forbid')
    release_id:str
    expected_revision:int=Field(ge=0)


class Cancel(BaseModel):
    expected_revision:int=Field(ge=0)


class Poll(BaseModel):
    agent_digest:str|None=Field(default=None,pattern=r"^[a-f0-9]{64}$")


class Report(BaseModel):
    model_config=ConfigDict(extra='forbid')
    status:Literal['VERIFYING','ROLLING_BACK','SUCCEEDED','FAILED']
    error:Literal['upgrade_failed','rollback_failed']|None=None


def release_view(r):return {**{k:getattr(r,k) for k in ('id','version','protocol_version','capabilities')},'created_at':utc(r.created_at)}
def item_view(i):return {k:getattr(i,k) for k in ('id','release_id','status','error','created_at','updated_at')}
def slots(s,w):return list(s.scalars(select(Slot).where(Slot.worker_id==w.id)))
def current(s,w):return s.scalar(select(WorkerUpgrade).where(WorkerUpgrade.worker_id==w.id).order_by(WorkerUpgrade.created_at.desc()).limit(1))
def updater_online(w):return bool(w.updater_seen_at and timedelta(0)<=now()-utc(w.updater_seen_at)<timedelta(seconds=30))
def transition(w,i,state,error=None):
    w.upgrade_state=i.status=state;w.upgrade_revision+=1;i.error=error;i.updated_at=now()


def build_release():
    from worker.version import SOFTWARE_VERSION,PROTOCOL_VERSION,CAPABILITIES
    root=Path(__file__).resolve().parents[2];buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for package in ('worker','runner','node_agent'):
            for path in sorted((root/package).rglob('*.py')):
                if path.is_symlink() or '__pycache__' in path.parts:continue
                info=zipfile.ZipInfo(path.relative_to(root).as_posix(),date_time=(2020,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                archive.writestr(info,path.read_bytes())
    payload=buffer.getvalue()
    if len(payload)>2*1024*1024:raise HTTPException(400,'release exceeds 2 MiB')
    return WorkerRelease(id=hashlib.sha256(payload).hexdigest(),version=SOFTWARE_VERSION,protocol_version=PROTOCOL_VERSION,capabilities=CAPABILITIES,archive=payload)


def install(app,db,admin,worker_auth):
    def machine(s,identity):
        w=s.get(Worker,identity)
        if not w or w.deleted_at:raise HTTPException(404,'node not found')
        return w

    @app.get('/api/v1/admin/worker-releases')
    def releases(user=Depends(admin),s=Depends(db)):
        return [release_view(r) for r in s.scalars(select(WorkerRelease).order_by(WorkerRelease.created_at.desc()))]

    @app.post('/api/v1/admin/worker-releases',status_code=201)
    def publish(user=Depends(admin),s=Depends(db)):
        candidate=build_release();lock(s);r=s.get(WorkerRelease,candidate.id)
        if not r:
            r=candidate;s.add(r);audit(s,user,'worker-release.publish','worker-releases',{'release_id':r.id});s.commit()
        return release_view(r)

    @app.get('/api/v1/admin/nodes/{identity}/upgrades')
    def listing(identity:str,user=Depends(admin),s=Depends(db)):
        w=machine(s,identity)
        return {'revision':w.upgrade_revision,'updater_online':updater_online(w),'state':w.upgrade_state,
                'slots':[{'id':x.id,'gpu_index':x.gpu_index,'online':online(x),'protocol_version':x.protocol_version,'software':x.software} for x in slots(s,w)],
                'history':[item_view(i) for i in s.scalars(select(WorkerUpgrade).where(WorkerUpgrade.worker_id==w.id).order_by(WorkerUpgrade.created_at.desc()).limit(20))]}

    @app.post('/api/v1/admin/nodes/{identity}/upgrades',status_code=201)
    def start(identity:str,body:Start,user=Depends(admin),s=Depends(db)):
        lock(s);w=machine(s,identity)
        if body.expected_revision!=w.upgrade_revision:raise HTTPException(409,'upgrade changed; refresh')
        if w.upgrade_state in BLOCKING:raise HTTPException(409,'finish or cancel the current upgrade first')
        from .enrollment import pending
        if pending(w):raise HTTPException(409,'finish re-enrollment before upgrading')
        r=s.get(WorkerRelease,body.release_id)
        if not r:raise HTTPException(404,'release not found')
        devices=slots(s,w)
        if w.revoked_at or not devices or not updater_online(w):raise HTTPException(409,'install and connect the node updater first')
        if not all(online(x) for x in devices):raise HTTPException(409,'all registered GPU workers must be online before upgrading')
        release_reservations(s,devices)
        i=WorkerUpgrade(worker_id=w.id,release_id=r.id,slot_ids=[x.id for x in devices]);s.add(i);s.flush()
        transition(w,i,'DRAINING');audit(s,user,'worker-upgrade.request',w.id,{'upgrade_id':i.id,'release_id':r.id});s.commit();return item_view(i)

    @app.post('/api/v1/admin/nodes/{identity}/upgrades/{upgrade_id}/cancel')
    def cancel(identity:str,upgrade_id:str,body:Cancel,user=Depends(admin),s=Depends(db)):
        lock(s);w=machine(s,identity);i=current(s,w)
        if not i or i.id!=upgrade_id or body.expected_revision!=w.upgrade_revision:raise HTTPException(409,'upgrade changed; refresh')
        if i.status not in ('DRAINING','FAILED') or i.error=='rollback_failed':raise HTTPException(409,'installation or recovery is not confirmed; cannot resume scheduling')
        transition(w,i,'CANCELLED');audit(s,user,'worker-upgrade.cancel',w.id,{'upgrade_id':i.id});s.commit();return item_view(i)

    @app.post('/api/v1/worker/upgrades/poll')
    def poll(body:Poll=Poll(),worker=Depends(worker_auth),s=Depends(db)):
        lock(s);s.refresh(worker);worker.updater_seen_at=now();i=current(s,worker);devices=slots(s,worker)
        if i and i.status=='DRAINING' and all(online(x) and not held(s,x) for x in devices):transition(worker,i,'APPLYING')
        record=(worker.connection or {}).get('enrollment',{})
        if record.get('state')=='INSTALLING' and body.agent_digest:
            worker.connection={**worker.connection,'enrollment':{**record,'agent_seen_digest':body.agent_digest}}
        result=None
        if i and (i.status in ('APPLYING','VERIFYING','ROLLING_BACK') or (i.status=='FAILED' and i.error=='rollback_failed')):
            result={**item_view(i),'release':release_view(s.get(WorkerRelease,i.release_id)),
                    'gpu_indices':[x.gpu_index for x in devices]}
        s.commit();return {'upgrade':result}

    @app.get('/api/v1/worker/upgrades/{identity}/state')
    def state(identity:str,worker=Depends(worker_auth),s=Depends(db)):
        i=s.get(WorkerUpgrade,identity)
        if not i or i.worker_id!=worker.id:raise HTTPException(404,'upgrade not found')
        return item_view(i)

    @app.get('/api/v1/worker/upgrades/{identity}/archive')
    def archive(identity:str,worker=Depends(worker_auth),s=Depends(db)):
        i=s.get(WorkerUpgrade,identity)
        if not i or i.worker_id!=worker.id:raise HTTPException(404,'upgrade not found')
        if i.status not in ('APPLYING','VERIFYING'):raise HTTPException(409,'upgrade is not active')
        r=s.get(WorkerRelease,i.release_id);return Response(r.archive,media_type='application/zip',headers={'X-Content-SHA256':r.id})

    @app.post('/api/v1/worker/upgrades/{identity}/report')
    def report(identity:str,body:Report,worker=Depends(worker_auth),s=Depends(db)):
        lock(s);s.refresh(worker);i=current(s,worker)
        if not i or i.id!=identity:raise HTTPException(409,'upgrade is no longer current')
        if i.status==body.status:return item_view(i)
        recovering=i.status=='FAILED' and i.error=='rollback_failed' and body.status=='ROLLING_BACK'
        if i.status not in ('APPLYING','VERIFYING','ROLLING_BACK') and not recovering:raise HTTPException(409,'upgrade is no longer active')
        if i.status=='ROLLING_BACK' and body.status not in ('ROLLING_BACK','FAILED'):raise HTTPException(409,'upgrade is rolling back')
        if body.status=='SUCCEEDED':
            r=s.get(WorkerRelease,i.release_id);devices=slots(s,worker)
            from .node_agents import record, fresh
            agent=record(worker)
            if agent and (not fresh(agent) or agent.get('digest')!=r.id):
                raise HTTPException(409,'waiting for node Agent to report target release')
            if {x.id for x in devices}!=set(i.slot_ids) or not all(online(x) and (x.software or {}).get('build_digest')==r.id and x.software.get('version')==r.version and x.protocol_version==r.protocol_version and set(x.software.get('capabilities',[]))==set(r.capabilities) for x in devices):
                raise HTTPException(409,'waiting for every GPU worker to report target release')
        if body.status=='FAILED' and body.error is None:raise HTTPException(400,'failure reason is required')
        worker.updater_seen_at=now();transition(worker,i,body.status,body.error);s.commit();return item_view(i)
