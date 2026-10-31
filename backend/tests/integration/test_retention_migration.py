import os,subprocess,sys,uuid
import pytest
from sqlalchemy import text
from backend.app.core import Worker,Slot,ExecutionData,DataCleanup,DataCleanupItem,now,session_factory

@pytest.mark.skipif(not os.getenv('MIGRATION_DATABASE_URL'),reason='separate disposable migration database required')
def test_retention_upgrade_defaults_and_downgrade_guard():
    url=os.environ['MIGRATION_DATABASE_URL'];env={**os.environ,'DATABASE_URL':url}
    def migrate(action,target=None,ok=True):
        result=subprocess.run([sys.executable,'-m','alembic','-c','backend/alembic.ini',action,*([target] if target else [])],env=env,capture_output=True,text=True)
        assert (result.returncode==0)==ok,result.stdout+result.stderr
    migrate('upgrade','head');migrate('downgrade','g730b12')
    factory=session_factory(url);wid=str(uuid.uuid4())
    with factory() as s:
        s.execute(text("INSERT INTO workers(id,node_name,token_hash,enabled,max_concurrent,cpu_capacity,ram_gb_capacity,created_at) VALUES(:id,:id,:id,true,1,4,8,now())"),{'id':wid});s.commit()
    migrate('upgrade','head')
    with factory() as s:
        w=s.get(Worker,wid);assert not w.retention_enabled and w.retention_days==30 and w.retention_revision==0
        w.retention_enabled=True;s.commit()
    migrate('downgrade','g730b12',False)
    with factory() as s:s.get(Worker,wid).retention_enabled=False;s.commit()
    with factory() as s:
        slot=Slot(worker_id=wid,node_name=wid,gpu_index=0,queue_name=wid);s.add(slot);s.flush()
        data=ExecutionData(worker_id=wid,slot_id=slot.id,root_id='a'*64,root_path='/runs',entry_name='unknown',generation=str(uuid.uuid4()),scan_status='blocked');s.add(data);s.flush()
        op=DataCleanup(worker_id=wid,source='manual',storage_revision=0,confirmed=True,expires_at=now());s.add(op);s.flush()
        item=DataCleanupItem(operation_id=op.id,data_id=data.id,slot_id=slot.id,snapshot={},status='running');s.add(item);s.commit();iid=item.id
    migrate('downgrade','g730b12',False)
    with factory() as s:s.get(DataCleanupItem,iid).status='failed';s.commit()
    migrate('check');migrate('downgrade','g730b12');migrate('upgrade','head')
