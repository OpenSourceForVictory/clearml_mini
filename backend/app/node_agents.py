"""Node control plane. Execution leases and resource accounting remain in execution.py."""
import secrets
from datetime import timedelta
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from .core import Worker, Slot, now
from .scheduling import lock, utc
from .storage import held
from .administration import audit, release_reservations
from .node_resources import resource_source, resource_values, apply_resources, host_codes


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Settings(Strict):
    heartbeat_seconds: int = Field(default=5, ge=2, le=15)
    sample_seconds: int = Field(default=15, ge=5, le=120)
    programs: Literal['running', 'stopped'] = 'running'


class Configure(Settings):
    expected_revision: int = Field(ge=0)


class Register(Strict):
    resource_protocol: Literal[0, 1] = 0
    boot_id: str = Field(pattern=r'^[a-f0-9]{32}$')
    version: str = Field(min_length=1, max_length=40)
    digest: str = Field(default='', pattern=r'^([a-f0-9]{64})?$')


class Sample(Strict):
    status: Literal['ok', 'failed', 'waiting']
    sampled_at: float = Field(ge=0)
    values: dict[str, float | str] = Field(default_factory=dict, max_length=1024)


class DeviceSample(Strict):
    kind: Literal['gpu', 'npu']
    vendor: Literal['nvidia', 'huawei']
    device_id: str = Field(min_length=1, max_length=100)
    index: int = Field(ge=0, le=1024)
    model: str = Field(default='', max_length=100)
    sampled_at: float = Field(ge=0)
    status: Literal['ok', 'failed', 'waiting']
    memory_total_mb: int | None = Field(default=None, ge=0, le=10240000)
    memory_free_mb: int | None = Field(default=None, ge=0, le=10240000)
    utilization_percent: float | None = Field(default=None, ge=0, le=100)
    physical_card_id: int | None = Field(default=None, ge=0, le=1024)
    chip_id: int | None = Field(default=None, ge=0, le=1024)
    health: str = Field(default='', max_length=40)
    execution_ready: bool = False


class Program(Strict):
    name: str = Field(pattern=r'^mini-eval-worker[\w@.\-]*\.service$', max_length=150)
    state: Literal['active', 'inactive', 'failed', 'activating', 'deactivating', 'unknown']


class Event(Strict):
    sequence: int = Field(ge=1)
    code: Literal['started', 'config_applied', 'config_failed', 'program_started', 'program_stopped', 'program_failed', 'connection_restored']


class Heartbeat(Strict):
    session: str = Field(pattern=r'^[a-f0-9]{32}$')
    sequence: int = Field(ge=1)
    applied_revision: int = Field(ge=0)
    config_status: Literal['applied', 'waiting', 'failed'] = 'waiting'
    samples: dict[Literal['host', 'gpu', 'npu', 'disk', 'docker'], Sample] = Field(default_factory=dict, max_length=5)
    devices: list[DeviceSample] = Field(default_factory=list, max_length=128)
    programs: list[Program] = Field(default_factory=list, max_length=64)
    events: list[Event] = Field(default_factory=list, max_length=64)


class Permit(Strict):
    session: str
    revision: int = Field(ge=0)


def record(w):
    return (w.connection or {}).get('agent', {})


def save(w, value):
    w.connection = {**(w.connection or {}), 'agent': value}


def fresh(r):
    try:
        from datetime import datetime
        return timedelta(0) <= now()-utc(datetime.fromisoformat(r['seen_at'])) < timedelta(seconds=45)
    except (KeyError, ValueError, TypeError):
        return False


def admission(w):
    r = record(w)
    if not r: return True
    return (fresh(r) and r.get('desired', {}).get('programs', 'running') == 'running'
            and r.get('applied_revision', -1) == r.get('revision', 0) and r.get('config_status') == 'applied')


def view(w):
    r = record(w)
    if not r: return {'status': 'not_installed', 'revision': 0, 'desired': Settings().model_dump()}
    return {k: v for k, v in {**r, 'status': 'online' if fresh(r) else 'offline'}.items()
            if k not in ('session', 'boot_id')}


def busy(s, w):
    return any(held(s, x) for x in s.scalars(select(Slot).where(Slot.worker_id == w.id)))


def response(s, w, r):
    upgrading = w.upgrade_state in ('DRAINING', 'APPLYING', 'VERIFYING', 'ROLLING_BACK', 'FAILED')
    return {'revision': r['revision'], 'desired': r['desired'], 'upgrade_blocked': upgrading,
            'busy': busy(s, w), 'event_ack': r.get('event_ack', 0),
            'storage_revision': w.storage_revision, 'runtime_revision': w.runtime_revision,
            'host_revision': w.host_revision}


def install(app, db, admin, worker_auth):
    def node(s, identity):
        w = s.get(Worker, identity)
        if not w or w.revoked_at: raise HTTPException(404, 'node not found')
        return w

    @app.get('/api/v1/admin/nodes/{identity}/agent')
    def status(identity: str, user=Depends(admin), s=Depends(db)):
        return view(node(s, identity))

    @app.put('/api/v1/admin/nodes/{identity}/agent')
    def configure(identity: str, body: Configure, user=Depends(admin), s=Depends(db)):
        lock(s); w = node(s, identity); s.refresh(w, with_for_update=True); r = record(w)
        if not r: raise HTTPException(409, 'install node Agent first')
        if body.expected_revision != r['revision']: raise HTTPException(409, 'agent configuration changed; refresh')
        if w.upgrade_state in ('DRAINING','APPLYING','VERIFYING','ROLLING_BACK','FAILED'):
            raise HTTPException(409, 'resolve upgrade before changing Agent configuration')
        # A late stop must finish before a new running intent can admit work.
        if r['desired']['programs'] == 'stopped' and body.programs == 'running' and (
                r.get('applied_revision') != r['revision'] or r.get('config_status') != 'applied'):
            raise HTTPException(409, 'wait for stopped acknowledgement before resuming')
        r = {**r, 'revision': r['revision']+1, 'desired': body.model_dump(exclude={'expected_revision'})}
        save(w, r)
        release_reservations(s, list(s.scalars(select(Slot).where(Slot.worker_id == w.id))))
        audit(s, user, 'agent.configure', w.id, r['desired']); s.commit()
        return view(w)

    @app.post('/api/v1/agent/register')
    def register(body: Register, w=Depends(worker_auth), s=Depends(db)):
        lock(s); s.refresh(w, with_for_update=True); r = record(w)
        if r.get('boot_id') == body.boot_id:
            return {'session': r['session'], **response(s, w, r)}
        r = {**r, 'boot_id': body.boot_id, 'session': secrets.token_hex(16), 'sequence': 0,
             'version': body.version, 'digest': body.digest, 'seen_at': None,
             'resource_protocol': body.resource_protocol, 'samples': {},
             'revision': r.get('revision', 0), 'desired': r.get('desired', Settings().model_dump()),
             'config_status': 'waiting', 'applied_revision': -1}
        save(w, r)
        for slot in s.scalars(select(Slot).where(Slot.worker_id == w.id)):
            slot.telemetry_at = slot.free_vram_mb = slot.gpu_utilization = None
        s.commit()
        return {'session': r['session'], **response(s, w, r)}

    @app.post('/api/v1/agent/heartbeat')
    def heartbeat(body: Heartbeat, w=Depends(worker_auth), s=Depends(db)):
        s.refresh(w, with_for_update=True); r = record(w)
        if r.get('session') != body.session: raise HTTPException(409, 'agent session superseded')
        if body.sequence <= r.get('sequence', 0): return response(s, w, r)
        if body.applied_revision > r['revision']: raise HTTPException(409, 'unknown configuration revision')
        # No unbounded arbitrary metrics strings or raw command output.
        if any(len(k)>64 or len(str(v)) > 160 for sample in body.samples.values() for k,v in sample.values.items()):
            raise HTTPException(422, 'sample value too long')
        events = list(r.get('events', [])); ack = r.get('event_ack', 0)
        for e in sorted(body.events, key=lambda x: x.sequence):
            if e.sequence > ack:
                events.append(e.model_dump()); ack = e.sequence
        r = {**r, **body.model_dump(exclude={'session', 'events'}), 'events': events[-64:],
             'event_ack': ack, 'seen_at': now().isoformat()}
        save(w, r)
        apply_resources(s, w)
        s.commit()
        return response(s, w, r)

    @app.post('/api/v1/agent/stop-permit')
    def permit(body: Permit, w=Depends(worker_auth), s=Depends(db)):
        lock(s); s.refresh(w, with_for_update=True); r = record(w)
        if r.get('session') != body.session:
            raise HTTPException(409, 'agent session superseded')
        if r.get('revision') != body.revision:
            return {'allowed': False}
        return {'allowed': bool(fresh(r) and r['desired']['programs'] == 'stopped'
                                and not busy(s, w) and w.upgrade_state not in ('DRAINING','APPLYING','VERIFYING','ROLLING_BACK','FAILED'))}
