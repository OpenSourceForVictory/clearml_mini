"""Run only against an empty, dedicated MIGRATION_TEST_DATABASE_URL."""
import os
import subprocess
import sys
from sqlalchemy import create_engine, text
url=os.environ['MIGRATION_TEST_DATABASE_URL']
engine=create_engine(url)
def migrate(*args):
    return subprocess.run([sys.executable,'-m','alembic','-c','backend/alembic.ini',*args],env={**os.environ,'DATABASE_URL':url},capture_output=True,text=True).returncode
assert migrate('upgrade','e518f10')==0
with engine.begin() as c:
    c.execute(text("""INSERT INTO client_templates(id,name,version,image,command,protocols,enabled,created_at) VALUES ('t','legacy','1','test:1','["true"]','["http"]',true,now())"""))
assert migrate('upgrade','head')==0
with engine.begin() as c:
    assert c.execute(text("SELECT runtime FROM client_templates WHERE id='t'")).scalar() is None
    c.execute(text("UPDATE client_templates SET runtime='{}' WHERE id='t'"))
assert migrate('downgrade','e518f10')!=0
with engine.begin() as c:
    c.execute(text("UPDATE client_templates SET runtime=NULL WHERE id='t'"))
    c.execute(text("INSERT INTO workers(id,node_name,token_hash,enabled,max_concurrent,cpu_capacity,ram_gb_capacity,created_at,runtime_revision) VALUES ('w','w','hash',true,1,4,8,now(),1)"))
assert migrate('downgrade','e518f10')!=0
with engine.begin() as c:c.execute(text("UPDATE workers SET runtime_revision=0 WHERE id='w'"))
assert migrate('downgrade','e518f10')==0
assert migrate('upgrade','head')==0
print('Runtime migration: legacy preservation, both downgrade guards, downgrade and re-upgrade passed.')
