from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS
import pytest
from backend.app import execution_timing as t

AT=datetime(2026,1,1,tzinfo=timezone.utc)
def run(**kw):
    return NS(**dict(dict(id='r',status='RUNNING',enqueued_at=AT,started_at=None,finished_at=None,attempt=1,exit_code=None),**kw))
def job(**kw):return NS(**dict(dict(created_at=AT-timedelta(seconds=10),client_spec={}),**kw))
def test_attempt_waiting_start_failure_cancel_and_retry():
    value=t.attempt(job(),run(),AT+timedelta(seconds=5))
    assert value['execution']['seconds']==5 and value['waiting']['seconds']==10
    assert value['waiting']['quality']=='fallback'
    failed=t.attempt(job(),run(status='FAILED',finished_at=AT+timedelta(seconds=3)),AT+timedelta(days=3))
    assert failed['execution']['seconds']==3
    cancelled=t.attempt(job(),run(status='CANCELLED',enqueued_at=None,finished_at=AT),AT)
    assert cancelled['execution']['state']=='not_started' and cancelled['execution']['seconds'] is None
    assert cancelled['waiting']['seconds']==10
    retry=t.attempt(job(),run(attempt=2,enqueued_at=None,status='PENDING'),AT)
    assert retry['waiting']['state']=='unknown'
    assert t.attempt(job(),run(attempt=2),AT,AT-timedelta(seconds=2))['waiting']['seconds']==2
    assert t.attempt(job(),run(status='FAILED'),AT)['execution']['state']=='unknown'
    for status in ('SUCCEEDED','FAILED','TIMED_OUT'):
        historical=t.attempt(job(),run(status=status,enqueued_at=None),AT)
        assert historical['execution']['state']=='unknown'
        assert historical['execution']['seconds'] is None
        assert historical['waiting']['seconds'] is None
    legacy_running=t.attempt(job(),run(enqueued_at=None,started_at=AT),AT+timedelta(seconds=5))
    assert legacy_running['waiting']['state']=='unknown'
    assert t.attempt(job(),run(enqueued_at=AT+timedelta(seconds=1)),AT)['execution']['seconds'] is None


def test_batch_parallel_not_sum_retry_and_empty():
    batch=NS(created_at=AT)
    first=run(status='SUCCEEDED',finished_at=AT+timedelta(seconds=10))
    second=run(status='FAILED',finished_at=AT+timedelta(seconds=15))
    assert t.batch(batch,[first,second],AT)['elapsed']['seconds']==15
    assert t.batch(batch,[first,run(status='PENDING')],AT+timedelta(seconds=25))['elapsed']['seconds']==25
    assert t.batch(batch,[],AT)['elapsed']['state']=='unknown'
    assert t.batch(batch,[run(status='FAILED')],AT)['elapsed']['state']=='unknown'


@pytest.mark.parametrize('value',[None,5,'invalid'])
def test_invalid_utc(value):assert t.utc(value) is None

def test_utc_normalize_zero_and_episode_fallback():
    assert t.utc(AT.replace(tzinfo=None))==AT
    assert t.utc('2026-01-01T00:00:00Z')==AT
    template={'integration_mode':'sdk-command'}
    assert t.episode({},None,'RUNNING',AT) is None
    assert t.episode(template,None,'RUNNING',AT)['state']=='not_started'
    row=NS(started={'status':'STARTED'},result=None)
    assert t.episode(template,row,'RUNNING',AT)['state']=='running'
    assert t.episode(template,row,'FAILED',AT)['state']=='interrupted'
    row.started[t.RECEIVED]=AT.isoformat()
    assert t.episode(template,row,'RUNNING',AT)['seconds']==0
    assert t.RECEIVED not in t.original(row.started)
    row.result={'runtime':0}
    assert t.episode(template,row,'FAILED',AT)['seconds']==0
    for value in (-1,True,float('nan'),float('inf'),None):
        row.result={'runtime':value}
        assert t.episode(template,row,'FAILED',AT)['state']=='unknown'
    row.result={'timing':{'started_at':AT.isoformat(),'finished_at':AT.isoformat(),'duration_seconds':3,'clock_adjusted':True}}
    assert t.episode(template,row,'FAILED',AT)['seconds']==3


def test_bulk_query_is_bounded():
    class Session:
        def __init__(self):self.calls=[]
        def execute(self,q):self.calls.append(q);return NS(all=lambda:[])
    s=Session()
    assert t.pending_starts(s,[])=={} and not s.calls
    t.pending_starts(s,[str(i) for i in range(1001)])
    assert len(s.calls)==3
