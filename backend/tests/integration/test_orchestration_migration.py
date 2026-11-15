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
    # An active orchestration must not be handed to an older scheduler.
    from backend.app.core import User,Pool,Job,Run,EvaluationBatch,EvaluationOrchestration
    from sqlalchemy.orm import Session
    with Session(engine) as s:
        s.add(User(id='migration-owner',username='migration-owner',password_hash='unused'))
        s.add(Pool(id='migration-pool',name='migration-pool',gpu_model='virtual'));s.flush()
        batch=EvaluationBatch(owner_id='migration-owner',title='active migration guard',max_concurrent=1)
        s.add(batch);s.flush();batch_id=batch.id
        job=Job(owner_id='migration-owner',pool_id='migration-pool',batch_id=batch.id,title='guard',benchmark='mock',model_ref='test',episodes=1)
        s.add(job);s.flush();job_id=job.id
        s.add(Run(job_id=job.id,attempt=1));s.add(EvaluationOrchestration(batch_id=batch.id,request_digest='guard',definition={},stop_after_batch=True));s.commit()
    with pytest.raises(subprocess.CalledProcessError) as blocked:migrate('downgrade','q730f12')
    assert 'stop and drain all orchestrations' in blocked.value.stderr
    with Session(engine) as s:
        s.query(Run).filter_by(job_id=job_id).delete();s.query(Job).filter_by(id=job_id).delete()
        s.query(EvaluationOrchestration).filter_by(batch_id=batch_id).delete();s.query(EvaluationBatch).filter_by(id=batch_id).delete()
        s.query(Pool).filter_by(id='migration-pool').delete();s.query(User).filter_by(id='migration-owner').delete();s.commit()
    migrate('downgrade','n407c89')
    assert 'task_type' not in {x['name'] for x in inspect(engine).get_columns('client_templates')}
    assert 'owner_id' not in {x['name'] for x in inspect(engine).get_columns('server_templates')}
    migrate('upgrade','head')
    with engine.begin() as c:
        c.execute(text("DELETE FROM client_templates WHERE id='migration-client'"))
        c.execute(text("DELETE FROM server_templates WHERE id='migration-server'"))
    engine.dispose()
