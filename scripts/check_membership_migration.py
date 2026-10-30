"""Destructive migration check; requires a dedicated empty MIGRATION_TEST_DATABASE_URL."""
import os
import subprocess
import sys
from sqlalchemy import create_engine, text
url = os.environ['MIGRATION_TEST_DATABASE_URL']
engine = create_engine(url)
env = {**os.environ, 'DATABASE_URL': url}
def migrate(revision):
    return subprocess.run([sys.executable, '-m', 'alembic', '-c', 'backend/alembic.ini', *revision], env=env, capture_output=True, text=True)
assert migrate(['upgrade', 'c396d08']).returncode == 0
with engine.begin() as c:
    c.execute(text("INSERT INTO resource_pools (id,name,gpu_model,min_vram_gb,cpu_per_job,ram_gb_per_job,enabled,labels) VALUES ('old','old','virtual',0,1,1,true,'{}')"))
    c.execute(text("INSERT INTO gpu_slots (id,node_name,gpu_index,pool_id,queue_name,enabled) VALUES ('legacy','legacy',0,'old','legacy',true)"))
assert migrate(['upgrade', 'head']).returncode == 0
with engine.begin() as c:
    row = c.execute(text("SELECT pool_id,desired_pool_id,membership_revision,membership_applied_revision FROM gpu_slots WHERE id='legacy'")).one()
    assert tuple(row) == ('old','old',0,0)
    c.execute(text("UPDATE gpu_slots SET pool_id=NULL WHERE id='legacy'"))
assert migrate(['downgrade','c396d08']).returncode != 0
with engine.begin() as c:
    c.execute(text("UPDATE gpu_slots SET pool_id='old',membership_revision=1 WHERE id='legacy'"))
assert migrate(['downgrade','c396d08']).returncode != 0
with engine.begin() as c:
    c.execute(text("UPDATE gpu_slots SET membership_applied_revision=1 WHERE id='legacy'"))
from sqlalchemy.exc import IntegrityError
try:
    with engine.begin() as c:
        c.execute(text("UPDATE gpu_slots SET desired_pool_id='missing' WHERE id='legacy'"))
except IntegrityError:
    pass
else:
    raise AssertionError('desired pool foreign key is missing')
assert migrate(['downgrade','c396d08']).returncode == 0
assert migrate(['upgrade','head']).returncode == 0
print('Migration backfill, nullable membership, guarded downgrade and re-upgrade passed.')
