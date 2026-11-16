"""Run only against a dedicated disposable resource migration database."""
import os
import subprocess
import pytest
from sqlalchemy import create_engine,text


@pytest.mark.skipif(not os.getenv('RESOURCE_MIGRATION_URL'),reason='dedicated disposable resource migration database required')
def test_resource_contract_migration_and_downgrade_guard():
    url=os.environ['RESOURCE_MIGRATION_URL'];env={**os.environ,'DATABASE_URL':url}
    def migrate(*args):
        return subprocess.run(['.venv/bin/alembic','-c','backend/alembic.ini',*args],env=env,check=True,capture_output=True,text=True)
    migrate('upgrade','r841a23');engine=create_engine(url)
    try:
        with engine.begin() as c:
            c.execute(text("INSERT INTO client_templates(id,name,version,image,command,protocols,enabled,created_at) VALUES ('resource-migration','legacy-resource','1','test:1','[\"python\"]','[\"http\"]',true,CURRENT_TIMESTAMP)"))
        migrate('upgrade','head')
        with engine.begin() as c:
            assert c.execute(text("SELECT resource_requirements FROM client_templates WHERE id='resource-migration'")).scalar() is None
            c.execute(text("UPDATE client_templates SET resource_requirements=:contract WHERE id='resource-migration'"), {"contract":'{"version":1}'})
        with pytest.raises(subprocess.CalledProcessError) as blocked:migrate('downgrade','r841a23')
        assert 'resource contract data present' in blocked.value.stderr
        with engine.begin() as c:c.execute(text("UPDATE client_templates SET resource_requirements=NULL WHERE id='resource-migration'"))
        migrate('downgrade','r841a23');migrate('upgrade','head')
        with engine.begin() as c:
            assert c.execute(text("SELECT name FROM client_templates WHERE id='resource-migration'")).scalar()=='legacy-resource'
            c.execute(text("DELETE FROM client_templates WHERE id='resource-migration'"))
    finally:engine.dispose()
