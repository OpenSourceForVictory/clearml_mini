"""NPU PCI identities may repeat across hosts; physical GPU UUIDs may not."""
from importlib import import_module
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import IntegrityError

from backend.app.core import Slot
from backend.tests.test_execution import env


def credentials(client, admin, name):
    response = client.post('/api/v1/admin/worker-credentials', headers=admin,
                           json={'node_name': name, 'max_concurrent': 2})
    assert response.status_code == 201, response.text
    return {'Authorization': 'Bearer ' + response.json()['token']}


def register(client, token, *, kind, index, identity):
    return client.post('/api/v1/worker/register', headers=token, json={
        'accelerator_type': kind, 'gpu_index': index, 'gpu_uuid': identity,
        'gpu_model': 'Ascend 910B' if kind == 'npu' else 'RTX 4090',
        'vram_gb': 32 if kind == 'npu' else 24,
        'protocol_version': 11 if kind == 'npu' else 1,
    })


def test_npu_identity_is_node_scoped_and_reconnect_is_idempotent(env):
    main, client, auth = env
    first = credentials(client, auth['admin'], 'ascend-a')
    second = credentials(client, auth['admin'], 'ascend-b')
    identity = 'ascend:0000:01:00.0:0'
    a = register(client, first, kind='npu', index=0, identity=identity)
    b = register(client, second, kind='npu', index=0, identity=identity)
    assert a.status_code == b.status_code == 200, (a.text, b.text)
    assert a.json()['slot_id'] != b.json()['slot_id']
    again = register(client, second, kind='npu', index=0, identity=identity)
    assert again.status_code == 200 and again.json()['slot_id'] == b.json()['slot_id']
    collision = register(client, second, kind='npu', index=1, identity=identity)
    assert collision.status_code == 409
    with main.SessionLocal() as session:
        slots = list(session.scalars(select(Slot).where(Slot.gpu_uuid == identity)))
        assert {slot.node_name for slot in slots} == {'ascend-a', 'ascend-b'}


def test_gpu_identity_stays_globally_unique(env):
    _, client, auth = env
    first = credentials(client, auth['admin'], 'gpu-a')
    second = credentials(client, auth['admin'], 'gpu-b')
    identity = 'GPU-12345678'
    assert register(client, first, kind='gpu', index=0, identity=identity).status_code == 200
    assert register(client, second, kind='gpu', index=0, identity=identity).status_code == 409


def test_migration_replaces_global_index_without_rewriting_slots():
    migration = import_module('backend.alembic.versions.d28f6a1_scope_npu_slot_identity')
    engine = create_engine('sqlite://')
    with engine.begin() as connection:
        connection.execute(text('''CREATE TABLE gpu_slots (
            id TEXT PRIMARY KEY, node_name TEXT, gpu_uuid TEXT,
            accelerator_type TEXT, deleted_at TEXT)'''))
        connection.execute(text('CREATE UNIQUE INDEX uq_live_slot_uuid ON gpu_slots (gpu_uuid) WHERE deleted_at IS NULL'))
        connection.execute(text("INSERT INTO gpu_slots VALUES ('old', 'npu-a', 'ascend:0000:01:00.0:0', 'npu', NULL)"))
        with Operations.context(MigrationContext.configure(connection)):
            migration.upgrade()
        connection.execute(text("INSERT INTO gpu_slots VALUES ('new', 'npu-b', 'ascend:0000:01:00.0:0', 'npu', NULL)"))
        with pytest.raises(IntegrityError):
            connection.execute(text("INSERT INTO gpu_slots VALUES ('bad-npu', 'npu-b', 'ascend:0000:01:00.0:0', 'npu', NULL)"))
        connection.execute(text("INSERT INTO gpu_slots VALUES ('gpu-a', 'gpu-a', 'GPU-1', 'gpu', NULL)"))
        with pytest.raises(IntegrityError):
            connection.execute(text("INSERT INTO gpu_slots VALUES ('bad-gpu', 'gpu-b', 'GPU-1', 'gpu', NULL)"))
        with Operations.context(MigrationContext.configure(connection)):
            with pytest.raises(RuntimeError, match='global UUID uniqueness'):
                migration.downgrade()
