"""Environment acknowledgements and independently sampled resources use distinct windows."""
from datetime import timedelta
from types import SimpleNamespace as N

from backend.app.core import now
from backend.app import host_environments, image_runtime, storage
from backend.app.freshness import ENVIRONMENT_REPORT_SECONDS, RESOURCE_SAMPLE_SECONDS, recent
from backend.app.node_resources import resource_values
from backend.app.scheduling import online, telemetry_fresh


def test_environment_reports_allow_ordinary_heartbeat_jitter_but_expire():
    at=now()
    worker=N(storage_revision=1,runtime_revision=1,host_revision=1,host_environments={'demo':{}})
    slot=N(protocol_version=14,storage_applied_revision=1,
           storage_report={'revision':1,'status':'applied'},
           runtime_report={'revision':1,'applied_revision':1},
           host_report={'revision':1,'applied_revision':1,'environments':{'demo':{'ready':True}}})
    spec={'template':{'execution_mode':'host','host_runtime':{'environment_name':'demo','assets':[]}}}
    for age,expected in ((30,True),(ENVIRONMENT_REPORT_SECONDS-1,True),(ENVIRONMENT_REPORT_SECONDS+1,False)):
        timestamp=at-timedelta(seconds=age)
        slot.storage_reported_at=slot.runtime_reported_at=slot.host_reported_at=timestamp
        assert storage.ready(worker,slot,at) is expected
        assert image_runtime.ready(worker,slot,at) is expected
        assert (not host_environments.blockers(worker,slot,spec,at)) is expected
    slot.storage_reported_at=slot.runtime_reported_at=slot.host_reported_at=at+timedelta(seconds=1)
    assert not storage.ready(worker,slot,at)
    assert not image_runtime.ready(worker,slot,at)
    assert host_environments.blockers(worker,slot,spec,at)
    slot.storage_reported_at=slot.runtime_reported_at=slot.host_reported_at=at
    worker.host_revision=2
    assert host_environments.blockers(worker,slot,spec,at)
    worker.host_revision=1
    slot.enabled=True
    slot.last_seen_at=at-timedelta(seconds=35)
    assert storage.ready(worker,slot,at) and not online(slot,at)


def test_resource_samples_keep_original_timestamp_and_reject_future_or_missing():
    at=now()
    slot=N(free_vram_mb=1024,gpu_utilization=10)
    for age,expected in ((20,True),(RESOURCE_SAMPLE_SECONDS-1,True),(RESOURCE_SAMPLE_SECONDS+1,False)):
        slot.telemetry_at=at-timedelta(seconds=age)
        assert telemetry_fresh(slot,at) is expected
        assert recent(slot.telemetry_at,at,RESOURCE_SAMPLE_SECONDS) is expected
    slot.telemetry_at=at+timedelta(seconds=1)
    assert not telemetry_fresh(slot,at)
    slot.telemetry_at=None
    assert not telemetry_fresh(slot,at)
    record={'agent':{'seen_at':at.isoformat(),'resource_protocol':1,
                     'samples':{'host':{'sampled_at':(at-timedelta(seconds=30)).timestamp(),
                                        'status':'ok','values':{'cpu_count':8}}}}}
    worker=N(connection=record)
    assert resource_values(worker,'host',at)=={'cpu_count':8}
    worker.connection['agent']['samples']['host']['sampled_at']=(at-timedelta(seconds=46)).timestamp()
    assert resource_values(worker,'host',at) is None
