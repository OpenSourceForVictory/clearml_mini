"""Opt-in only: downgrades a dedicated disposable database."""
import os
import subprocess
import pytest
from sqlalchemy import create_engine, inspect, text


@pytest.mark.skipif(not os.getenv('ORCHESTRATION_MIGRATION_URL'),reason='dedicated disposable orchestration migration database required')
def test_template_migration_upgrade_and_downgrade():
    url=os.environ['ORCHESTRATION_MIGRATION_URL'];env={**os.environ,'DATABASE_URL':url}
    def migrate(*args):subprocess.run(['.venv/bin/alembic','-c','backend/alembic.ini',*args],env=env,check=True,capture_output=True,text=True)
    migrate('upgrade','n407c89');engine=create_engine(url)
    with engine.begin() as c:
        c.execute(text("INSERT INTO client_templates(id,name,version,image,command,protocols,enabled,created_at) VALUES ('migration-client','legacy','1','test:1','[\"python\"]','[\"http\"]',true,CURRENT_TIMESTAMP)"))
        c.execute(text("INSERT INTO server_templates(id,name,version,definition,enabled,archived,created_at) VALUES ('migration-server','legacy','1','{}',true,false,CURRENT_TIMESTAMP)"))
    migrate('upgrade','head')
    with engine.connect() as c:
        row=c.execute(text("SELECT task_type,status,revision,platform_bindings FROM client_templates WHERE id='migration-client'")).one()
        assert tuple(row[:3])==('evaluation','published',0) and row[3]==[]
        row=c.execute(text("SELECT status,revision,owner_id,available_to_members FROM server_templates WHERE id='migration-server'")).one()
        assert tuple(row)==('published',0,None,False)
    migrate('downgrade','n407c89')
    assert 'task_type' not in {x['name'] for x in inspect(engine).get_columns('client_templates')}
    assert 'owner_id' not in {x['name'] for x in inspect(engine).get_columns('server_templates')}
    migrate('upgrade','head')
    with engine.begin() as c:
        c.execute(text("DELETE FROM client_templates WHERE id='migration-client'"))
        c.execute(text("DELETE FROM server_templates WHERE id='migration-server'"))
    engine.dispose()
