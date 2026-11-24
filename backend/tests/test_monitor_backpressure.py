"""Request-local diagnostic caching must not become scheduling state."""
from sqlalchemy import event,select
from backend.tests.test_execution import env
from backend.tests.test_orchestrations import setup,submit,healthy
from backend.app.core import Job,User,Pool,ServiceInstance
from backend.app.preflight import DiagnosticContext


def test_managed_diagnostics_reuse_queries_and_refresh_health_next_request(env,monkeypatch):
    main,_,_=env;st,ss,_,_,body=setup(env,monkeypatch)
    b=submit(env,body);healthy(env,st,ss)
    with main.SessionLocal() as s:
        job=s.get(Job,b['cases'][0]['job_id']);owner=s.get(User,job.owner_id);pool=s.get(Pool,job.pool_id)
        context=DiagnosticContext(s)
        context.inspect(job,owner,pool)
        assert context.managed_endpoint_states
        assert any(context.managed_endpoint_states.values())
        sql=[]
        def query(*args):sql.append(args[2])
        engine=main.SessionLocal.kw['bind'];event.listen(engine,'before_cursor_execute',query)
        try:
            for _ in range(100):context.inspect(job,owner,pool)
        finally:event.remove(engine,'before_cursor_execute',query)
        assert sql==[],sql[:3]
        instance=s.scalar(select(ServiceInstance).where(ServiceInstance.orchestration_batch_id==b['id']))
        instance.health_ready=False;s.commit()
        refreshed=DiagnosticContext(s)
        refreshed.inspect(job,owner,pool)
        assert not any(refreshed.managed_endpoint_states.values())


def test_managed_cache_distinguishes_service_sets_batches_and_default(env,monkeypatch):
    from backend.app import endpoints
    main,_,_=env;*_,body=setup(env,monkeypatch);b=submit(env,body)
    calls=[]
    def inspect(spec,held,session):calls.append(dict(spec));return []
    monkeypatch.setattr(endpoints,'inspect',inspect)
    with main.SessionLocal() as s:
        job=s.get(Job,b['cases'][0]['job_id']);owner=s.get(User,job.owner_id);pool=s.get(Pool,job.pool_id)
        context=DiagnosticContext(s);base={**job.client_spec,'service_mode':'managed'}
        variants=[{'service_keys':['a','b']},{'service_keys':['b','a','a']},{'service_keys':['a']},
                  {'service_keys':[]},{},{'orchestration_batch_id':'other'}]
        for variant in variants:
            job.client_spec={k:v for k,v in base.items() if k!='service_keys'}|variant
            with s.no_autoflush:context.inspect(job,owner,pool)
        assert len(calls)==5
        job.client_spec={**base,'service_mode':'none'}
        with s.no_autoflush:
            context.inspect(job,owner,pool);context.inspect(job,owner,pool)
        assert len(calls)==7
