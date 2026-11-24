import pytest
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session, sessionmaker
from backend.app.core import Run
from scripts.scheduler_benchmark import seed, measure, main


@pytest.mark.parametrize('values',[(-1,0),(0,10001),(True,0)])
def test_invalid_seed(values):
    with pytest.raises(ValueError):seed(None,*values)


def test_repeat_isolation_and_real_reservation(tmp_path,monkeypatch):
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL','2')
    engine=create_engine('sqlite:///'+str(tmp_path/'bench.db'))
    at=seed(engine,3,5)
    first=measure(sessionmaker(bind=engine),at)
    second=measure(sessionmaker(bind=engine),at)
    assert first['admitted']==second['admitted']==1
    assert first['sql_count']==second['sql_count']
    assert first['objects_loaded']>0
    with Session(engine) as s:
        assert s.scalar(select(func.count()).select_from(Run).where(Run.status=='RESERVED'))==0
    engine.dispose()


def test_empty_queue_and_input_rejection(tmp_path):
    engine=create_engine('sqlite:///'+str(tmp_path/'bench.db'))
    at=seed(engine,0,0)
    assert measure(sessionmaker(bind=engine),at)['admitted']==0
    with pytest.raises(SystemExit):main(['--history','-1','--output',str(tmp_path/'out.json')])
    engine.dispose()


@pytest.mark.parametrize('failure',['body','connect','identity'])
def test_owned_container_cleanup(monkeypatch,failure):
    import json
    import scripts.scheduler_benchmark as bench
    commands=[]; label={}
    def run(cmd,**kwargs):
        commands.append(cmd)
        if cmd[1]=='run': label['value']=cmd[cmd.index('--label')+1].split('=',1)[1]
    def output(cmd):
        return json.dumps([{'Id':'owned-id','Config':{'Labels':{'mini-eval.scheduler-benchmark':
            'changed' if failure=='identity' and len(commands)>1 else label['value']}},
            'NetworkSettings':{'Ports':{'5432/tcp':[{'HostPort':'12345'}]}}}]).encode()
    class FakeEngine:
        def connect(self):
            if failure=='connect': raise OSError('offline')
            return self
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,*args):pass
        def dispose(self):commands.append(['disposed'])
    monkeypatch.setattr(bench.subprocess,'run',run)
    monkeypatch.setattr(bench.subprocess,'check_output',output)
    monkeypatch.setattr(bench,'create_engine',lambda *a,**k:FakeEngine())
    monkeypatch.setattr(bench.time,'sleep',lambda *a:None)
    with pytest.raises((RuntimeError,OSError)):
        with bench.isolated_postgres():
            if failure=='body':raise RuntimeError('injected')
    removed=[c for c in commands if c[:2]==['docker','rm']]
    assert bool(removed)==(failure!='identity')
    if removed:assert removed[0][-1]=='owned-id'


def test_real_postgres_main_creates_and_removes_its_container(tmp_path,monkeypatch):
    import json, subprocess
    created=[]
    original=subprocess.run
    def track(cmd,**kwargs):
        if cmd[:2]==['docker','run']:created.append(cmd[cmd.index('--name')+1])
        return original(cmd,**kwargs)
    monkeypatch.setattr(subprocess,'run',track)
    output=tmp_path/'result.json'
    main(['--pending','2','--repeat','1','--output',str(output)])
    data=json.loads(output.read_text())
    assert data['samples'][0]['admitted']==1
    assert data['postgres'].startswith('16.')
    assert len(created)==1
    assert original(['docker','inspect',created[0]],capture_output=True).returncode!=0


def test_failed_main_preserves_failure_evidence(tmp_path,monkeypatch):
    from contextlib import contextmanager
    import json
    import scripts.scheduler_benchmark as bench
    @contextmanager
    def fail():
        raise OSError('test server unavailable')
        yield
    monkeypatch.setattr(bench,'isolated_postgres',fail)
    output=tmp_path/'failed.json'
    with pytest.raises(OSError):bench.main(['--output',str(output)])
    assert json.loads(output.read_text())['failure']=='OSError'
