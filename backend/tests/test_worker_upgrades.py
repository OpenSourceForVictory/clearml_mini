from datetime import timedelta
import io,zipfile,hashlib,json
from pathlib import Path
import pytest
from sqlalchemy import select
from backend.tests.test_execution import env,node,submit,claim
from backend.app.core import Worker,Slot,Run,now
from backend.app.scheduling import tick
from worker.updater import unpack


def setup(env):
    main,c,a=env;token,slots=node(env)
    with main.SessionLocal() as s:wid=s.get(Slot,slots[0]).worker_id
    r=c.post('/api/v1/admin/worker-releases',headers=a['admin']);assert r.status_code==201,r.text
    release=r.json()
    assert c.post('/api/v1/worker/upgrades/poll',headers=token).status_code==200
    return token,slots,wid,release


def test_upgrade_drains_all_work_and_checks_actual_reconnect(env):
    main,c,a=env;token,slots,wid,release=setup(env);job=submit(env);task=claim(env,token,slots[0]);assert task
    route='/api/v1/admin/nodes/'+wid+'/upgrades'
    assert c.post(route,headers=a['alice'],json={'release_id':release['id'],'expected_revision':0}).status_code==403
    r=c.post(route,headers=a['admin'],json={'release_id':release['id'],'expected_revision':0});assert r.status_code==201,r.text
    upgrade=r.json();assert upgrade['status']=='DRAINING'
    assert c.post('/api/v1/worker/upgrades/poll',headers=token).json()['upgrade'] is None
    assert c.post('/api/v1/worker/heartbeat',headers=token,json={'slot_id':slots[0],'execution_id':task['execution_id']}).status_code==200
    next_job=submit(env);assert tick(main.SessionLocal)==0
    assert c.post('/api/v1/worker/executions/'+task['execution_id']+'/complete',headers=token,json={'status':'FAILED','error':'finished fixture'}).status_code==200
    grant=c.post('/api/v1/worker/upgrades/poll',headers=token).json()['upgrade'];assert grant['id']==upgrade['id']
    assert c.post(route+'/'+upgrade['id']+'/cancel',headers=a['admin'],json={'expected_revision':2}).status_code==409
    report='/api/v1/worker/upgrades/'+upgrade['id']+'/report'
    assert c.post(report,headers=token,json={'status':'SUCCEEDED'}).status_code==409
    archive=c.get('/api/v1/worker/upgrades/'+upgrade['id']+'/archive',headers=token)
    assert hashlib.sha256(archive.content).hexdigest()==release['id']
    with main.SessionLocal() as s:
        for identity in slots:
            slot=s.get(Slot,identity);slot.software={'version':release['version'],'build_digest':release['id'],'capabilities':release['capabilities']};slot.protocol_version=release['protocol_version'];slot.last_seen_at=now()
        s.commit()
    assert c.post(report,headers=token,json={'status':'VERIFYING'}).status_code==200
    assert c.post(report,headers=token,json={'status':'SUCCEEDED'}).status_code==200
    assert c.post(report,headers=token,json={'status':'SUCCEEDED'}).status_code==200
    assert c.post(report,headers=token,json={'status':'ROLLING_BACK'}).status_code==409
    with main.SessionLocal() as s:assert s.get(Worker,wid).upgrade_state=='SUCCEEDED'


def test_unknown_updater_and_failed_rollback_cannot_resume(env):
    main,c,a=env;token,slots,wid,release=setup(env);route='/api/v1/admin/nodes/'+wid+'/upgrades'
    with main.SessionLocal() as s:s.get(Worker,wid).updater_seen_at=now()-timedelta(minutes=1);s.commit()
    body={'release_id':release['id'],'expected_revision':0}
    assert c.post(route,headers=a['admin'],json=body).status_code==409
    c.post('/api/v1/worker/upgrades/poll',headers=token)
    r=c.post(route,headers=a['admin'],json=body);identity=r.json()['id']
    c.post('/api/v1/worker/upgrades/poll',headers=token)
    path='/api/v1/worker/upgrades/'+identity+'/report'
    assert c.post(path,headers=token,json={'status':'ROLLING_BACK'}).status_code==200
    assert c.post(path,headers=token,json={'status':'SUCCEEDED'}).status_code==409
    assert c.post(path,headers=token,json={'status':'FAILED','error':'rollback_failed'}).status_code==200
    rev=c.get(route,headers=a['admin']).json()['revision']
    assert c.post(route+'/'+identity+'/cancel',headers=a['admin'],json={'expected_revision':rev}).status_code==409
    assert c.post(route,headers=a['admin'],json={**body,'expected_revision':rev}).status_code==409


def test_release_is_immutable_and_permissions_apply(env):
    _,c,a=env
    assert c.post('/api/v1/admin/worker-releases',headers=a['alice']).status_code==403
    first=c.post('/api/v1/admin/worker-releases',headers=a['admin']).json()
    second=c.post('/api/v1/admin/worker-releases',headers=a['admin']).json()
    assert first==second and len(c.get('/api/v1/admin/worker-releases',headers=a['admin']).json())==1


def test_archive_rejects_digest_and_traversal(tmp_path):
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:z.writestr('../bad.py','pass')
    payload=buffer.getvalue()
    with pytest.raises(ValueError,match='digest'):unpack(payload,'0'*64,tmp_path)
    with pytest.raises(ValueError,match='member'):unpack(payload,hashlib.sha256(payload).hexdigest(),tmp_path)
    assert not list(tmp_path.iterdir())


def test_reserved_work_returns_to_queue_and_cancel_uses_revision(env):
    main,c,a=env;token,slots,wid,release=setup(env);job=submit(env);assert tick(main.SessionLocal)==1
    route='/api/v1/admin/nodes/'+wid+'/upgrades'
    result=c.post(route,headers=a['admin'],json={'release_id':release['id'],'expected_revision':0}).json()
    with main.SessionLocal() as s:
        run=s.scalar(select(Run).where(Run.job_id==job));assert run.status=='PENDING' and run.slot_id is None
    assert tick(main.SessionLocal)==0
    assert c.post(route+'/'+result['id']+'/cancel',headers=a['admin'],json={'expected_revision':0}).status_code==409
    assert c.post(route+'/'+result['id']+'/cancel',headers=a['admin'],json={'expected_revision':1}).status_code==200
    assert tick(main.SessionLocal)==1


def test_upgrade_waits_for_every_registered_gpu(env):
    main,c,a=env;token,slots,wid,release=setup(env)
    second=c.post('/api/v1/worker/register',headers=token,json={'pool_id':'mock','gpu_index':1,'gpu_uuid':'mock:node:1','gpu_model':'virtual','vram_gb':0,'protocol_version':9}).json()['slot_id']
    route='/api/v1/admin/nodes/'+wid+'/upgrades'
    identity=c.post(route,headers=a['admin'],json={'release_id':release['id'],'expected_revision':0}).json()['id']
    grant=c.post('/api/v1/worker/upgrades/poll',headers=token).json()['upgrade'];assert sorted(grant['gpu_indices'])==[0,1]
    with main.SessionLocal() as s:
        slot=s.get(Slot,slots[0]);slot.software={'version':release['version'],'build_digest':release['id'],'capabilities':release['capabilities']};slot.protocol_version=release['protocol_version'];s.commit()
    assert c.post('/api/v1/worker/upgrades/'+identity+'/report',headers=token,json={'status':'SUCCEEDED'}).status_code==409
    other,other_slots=node(env,'other')
    assert c.get('/api/v1/worker/upgrades/'+identity+'/archive',headers=other).status_code==404
    assert c.post('/api/v1/worker/upgrades/'+identity+'/report',headers=other,json={'status':'VERIFYING'}).status_code==409


def test_registration_records_software_without_changing_identity(env):
    main,c,a=env;token,slots=node(env)
    body={'pool_id':'mock','gpu_index':0,'gpu_uuid':'mock:node:0','gpu_model':'virtual','vram_gb':0,'protocol_version':9,
          'software':{'version':'0.10.0','build_digest':'a'*64,'capabilities':['directory_mounts']}}
    r=c.post('/api/v1/worker/register',headers=token,json=body);assert r.status_code==200
    assert r.json()['slot_id']==slots[0]
    with main.SessionLocal() as s:assert s.get(Slot,slots[0]).software==body['software']
