from types import SimpleNamespace as N
from datetime import timedelta
import pytest
from backend.app.core import now
from backend.app.scheduling import eligibility_codes
from worker import accelerators

@pytest.mark.parametrize('kind,backends,protocol,expected',[
 ('gpu',[],11,'accelerator_type'),('npu',[],11,'accelerator_backend'),
 ('npu',['npu:docker:exclusive'],10,'accelerator_backend'),('npu',['npu:docker:exclusive'],11,None)])
def test_npu_backend_gates(kind,backends,protocol,expected):
 slot=N(disk_min_free_mb=None,protocol_version=protocol,execution_modes=['docker'],client_images=['img'],accelerator_type=kind,accelerator_backends=backends,gpu_model='910B',telemetry_at=now(),free_vram_mb=40000,gpu_utilization=0)
 job=N(client_spec={'template':{'image':'img'},'resources':{'accelerator_type':'npu'}},required_gpu_model=None,gpu_mode='exclusive',requested_vram_gb=0)
 codes=eligibility_codes(slot,N(),job,[],now())
 assert codes==([expected] if expected else [])
 slot.telemetry_at=now()-timedelta(seconds=60)
 if not expected:assert eligibility_codes(slot,N(),job,[],now())==['telemetry_stale']


def test_ascend_docker_only_assigned_device_and_readonly_driver(monkeypatch):
 monkeypatch.setattr(accelerators,'approved',lambda cfg:({'allow_shared':False},'/dev/davinci2'))
 monkeypatch.setattr(accelerators,'ascend_sample',lambda cfg:{'identity':'ascend:test:0'})
 cfg={'accelerator_type':'npu','gpu_index':2};task={'accelerator_type':'npu','device_index':2,'gpu_uuid':'ascend:test:0','gpu_mode':'exclusive'}
 args=accelerators.docker_args(cfg,task)
 assert args.count('--device')==4 and '/dev/davinci2:/dev/davinci2:rw' in args
 assert not any('/dev/davinci0' in v or 'NVIDIA' in v or '--privileged' in v for v in args)
 assert 'type=bind,source=/usr/local/Ascend/driver,target=/usr/local/Ascend/driver,readonly' in args
 for patch in [{'device_index':1},{'gpu_uuid':'ascend:wrong:0'},{'gpu_mode':'shared'}]:
  with pytest.raises(RuntimeError):accelerators.docker_args(cfg,{**task,**patch})
 assert accelerators.capabilities(cfg)==['npu:docker:exclusive']


def test_unapproved_npu_cannot_advertise_backend():
 assert accelerators.capabilities({'accelerator_type':'npu'})==[]
 with pytest.raises(RuntimeError):accelerators.approved({'accelerator_type':'npu'})


def test_npu_host_properties_never_allow_other_cards_or_root(monkeypatch):
 from worker import npu_host
 monkeypatch.setattr(npu_host,'approved',lambda cfg:({'host':{'approved':True,'user':'service'}},'/dev/davinci2'))
 monkeypatch.setattr(npu_host.os,'geteuid',lambda:0)
 monkeypatch.setattr(npu_host.pwd,'getpwnam',lambda name:N(pw_uid=1001,pw_gid=1001,pw_name=name))
 values=npu_host.properties({})
 assert 'DevicePolicy=closed' in values and 'User=service' in values
 assert 'DeviceAllow=/dev/davinci2 rw' in values
 assert len([x for x in values if x.startswith('DeviceAllow=')])==4
 assert not any('/dev/davinci0' in x for x in values)
 monkeypatch.setattr(npu_host.os,'geteuid',lambda:1001)
 with pytest.raises(RuntimeError,match='privileged'):npu_host.properties({})
 monkeypatch.setattr(npu_host.os,'geteuid',lambda:0)
 monkeypatch.setattr(npu_host.pwd,'getpwnam',lambda name:N(pw_uid=0))
 with pytest.raises(RuntimeError,match='root'):npu_host.properties({})


def test_npu_host_requires_separate_approval(monkeypatch):
 from worker import npu_host
 monkeypatch.setattr(npu_host,'approved',lambda cfg:({'approved':True},'/dev/davinci2'))
 with pytest.raises(RuntimeError,match='approval'):npu_host.settings({})


def test_npu_device_probe_fails_if_unassigned_card_opens(monkeypatch):
 from worker import npu_host
 from pathlib import Path
 monkeypatch.setattr(npu_host.os,'open',lambda *a:10)
 monkeypatch.setattr(npu_host.os,'close',lambda fd:None)
 monkeypatch.setattr(Path,'glob',lambda *a:iter([Path('/dev/davinci2'),Path('/dev/davinci3')]))
 with pytest.raises(RuntimeError,match='unassigned'):npu_host.verify_devices('/dev/davinci2')


def test_host_resource_contract_never_runs_on_old_worker():
 slot=N(disk_min_free_mb=None,protocol_version=10)
 job=N(client_spec={'template':{'execution_mode':'host','resource_requirements':{'version':1}}})
 assert eligibility_codes(slot,N(),job,[],now())==['client_runtime']
