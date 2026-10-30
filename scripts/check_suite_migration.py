"""Use a dedicated empty MIGRATION_TEST_DATABASE_URL database only."""
import os,subprocess,sys
from sqlalchemy import create_engine,text
url=os.environ['MIGRATION_TEST_DATABASE_URL']
engine=create_engine(url)
def migrate(*args):
    p=subprocess.run([sys.executable,'-m','alembic','-c','backend/alembic.ini',*args],env={**os.environ,'DATABASE_URL':url},capture_output=True,text=True)
    return p.returncode
assert migrate('upgrade','d407e09')==0
with engine.begin() as c:
    c.execute(text("INSERT INTO users(id,username,password_hash,role,active,created_at) VALUES ('u','u','x','user',true,now())"))
    c.execute(text("""INSERT INTO client_templates(id,name,version,image,command,protocols,enabled,created_at) VALUES ('t','t','1','test:1','["true"]','["http"]',true,now())"""))
    c.execute(text("INSERT INTO evaluation_suites(id,owner_id,name,version,template_id,cases,created_at) VALUES ('s','u','legacy','1','t',CAST(:cases AS jsonb),now())"), {'cases': '[{"case_id":"a","task_name":"a","episodes":1,"seed":0,"parameters":{}}]'})
assert migrate('upgrade','head')==0
with engine.begin() as c:
    assert tuple(c.execute(text("SELECT status,archived,revision FROM evaluation_suites WHERE id='s'")).one())==('published',False,0)
    c.execute(text("UPDATE evaluation_suites SET status='draft' WHERE id='s'"))
assert migrate('downgrade','d407e09')!=0
with engine.begin() as c:c.execute(text("UPDATE evaluation_suites SET status='published',archived=true WHERE id='s'"))
assert migrate('downgrade','d407e09')!=0
with engine.begin() as c:c.execute(text("UPDATE evaluation_suites SET archived=false WHERE id='s'"))
assert migrate('downgrade','d407e09')==0
assert migrate('upgrade','head')==0
print('Suite migration backfill, guarded downgrade and re-upgrade passed.')
