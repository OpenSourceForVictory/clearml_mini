import os,subprocess,sys,uuid
import pytest
from sqlalchemy import select
from backend.app.core import User,Pool,EvaluationBatch,Job,Run,session_factory

@pytest.mark.skipif(not os.getenv('MIGRATION_DATABASE_URL'),reason='separate disposable migration database required')
def test_batch_controls_upgrade_and_guarded_downgrade():
    url=os.environ['MIGRATION_DATABASE_URL'];env={**os.environ,'DATABASE_URL':url}
    def migrate(action,target,ok=True):
        r=subprocess.run([sys.executable,'-m','alembic','-c','backend/alembic.ini',action,target],env=env,capture_output=True,text=True)
        assert (r.returncode==0)==ok,r.stdout+r.stderr
        return r
    migrate('upgrade','head');factory=session_factory(url);suffix=uuid.uuid4().hex
    with factory() as s:
        u=User(username=suffix,password_hash='unused');p=Pool(name=suffix,gpu_model='virtual');s.add_all([u,p]);s.flush()
        b=EvaluationBatch(owner_id=u.id,title='old batch');s.add(b);s.flush()
        j=Job(owner_id=u.id,pool_id=p.id,batch_id=b.id,title='old',benchmark='mock',model_ref='test',episodes=1);s.add(j);s.flush()
        r=Run(job_id=j.id,attempt=1);s.add(r);s.commit();bid,jid,rid=b.id,j.id,r.id
    migrate('downgrade','f629a11');migrate('upgrade','head')
    with factory() as s:
        b=s.get(EvaluationBatch,bid);assert not b.scheduling_paused and b.control_revision==0 and b.control_updated_at is None
        b.scheduling_paused=True;s.commit()
    assert 'Drain executions' in migrate('downgrade','f629a11',False).stderr
    with factory() as s:
        b=s.get(EvaluationBatch,bid);b.scheduling_paused=False
        s.get(Run,rid).cleanup_confirmed=False;s.commit()
    migrate('downgrade','f629a11',False)
    with factory() as s:
        s.get(Run,rid).cleanup_confirmed=True;s.commit()
    migrate('downgrade','f629a11');migrate('upgrade','head')
