"""Separate latest observations from desired Agent configuration."""
from sqlalchemy import inspect, select
from sqlalchemy.orm import object_session
from .core import AgentObservation

OBSERVED = frozenset(('sequence', 'seen_at', 'samples', 'devices', 'programs',
                      'applied_revision', 'config_status', 'events', 'event_ack'))


def record(worker):
    config = (worker.connection or {}).get('agent', {})
    if not config:
        return config
    state = inspect(worker, raiseerr=False)
    session = object_session(worker) if state is not None else None
    if session is None:
        return config  # Detached/legacy in-memory snapshots have no report row.
    observation = session.get(AgentObservation, worker.id)
    if observation is None or observation.session != config.get('session'):
        return config
    return {**config, **observation.payload}


def save_config(worker, value):
    # Preserve legacy observations for fallback until the first registration or
    # migration creates the report row; never copy live reports back into Worker.
    legacy = (worker.connection or {}).get('agent', {})
    desired = {**{k:v for k,v in legacy.items() if k in OBSERVED},
               **{k:v for k,v in value.items() if k not in OBSERVED}}
    worker.connection = {**(worker.connection or {}), 'agent': desired}


def observation(session, worker, *, create=False):
    row = session.scalar(select(AgentObservation).where(AgentObservation.worker_id == worker.id)
                         .with_for_update().execution_options(populate_existing=True))
    if row is None and create:
        row = AgentObservation(worker_id=worker.id, payload={})
        session.add(row)
    return row


def reset(session, worker, value):
    row = observation(session, worker, create=True)
    row.session = value.get('session')
    row.payload = {k:v for k,v in value.items() if k in OBSERVED}
    session.flush()
