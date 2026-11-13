"""Filesystem swaps are real; service management and API outages are injected."""
import hashlib,io,json,zipfile
from pathlib import Path
import urllib.error
import pytest
from worker.updater import Updater,atomic


def fixture(tmp_path):
    app=tmp_path/'app';(app/'worker').mkdir(parents=True);(app/'runner').mkdir();(app/'worker/client.py').write_text('# old worker');(app/'runner/sdk.py').write_text('# old SDK')
    confdir=tmp_path/'config';confdir.mkdir();cfg=confdir/'gpu0.json'
    state=tmp_path/'recovery/state.json';state.parent.mkdir();state.write_text('null')
    value={'api_url':'http://unused','token':'private-test-token','node_name':'test','gpu_index':0,'state_path':str(state)};cfg.write_text(json.dumps(value))
    config={'app_dir':str(app),'state_dir':str(tmp_path/'upgrade-state'),'config_paths':[str(cfg)],'services':[{'scope':'user','name':'mini-eval-worker@test.service'}],'verify_timeout':.01}
    buffer=io.BytesIO()
    with zipfile.ZipFile(buffer,'w') as z:
        for name,text in {'worker/client.py':'# new worker','worker/version.py':'SOFTWARE_VERSION="test"','runner/sdk.py':'# new SDK'}.items():z.writestr(name,text)
    payload=buffer.getvalue();job={'id':'test-operation','release':{'id':hashlib.sha256(payload).hexdigest()},'gpu_indices':[0]}
    updater=Updater(config);calls=[]
    updater.control=lambda action:calls.append(action)
    class API:
        status='APPLYING'
        fail_success=False
        success_ack_lost=False
        def request(self,path,body=None,binary=False,get=False):
            if binary:return payload
            if path.endswith('/state'):return {'status':self.status}
            if path.endswith('/poll'):return {'upgrade':None if self.status in ('SUCCEEDED','FAILED') else job}
            if body['status']=='SUCCEEDED':
                if self.fail_success:raise urllib.error.URLError('verification unavailable')
                self.status='SUCCEEDED'
                if self.success_ack_lost:raise urllib.error.URLError('ACK lost')
            else:self.status=body['status']
            return {'status':self.status}
    updater.api=API()
    return updater,job,cfg,state,calls


def test_success_preserves_config_journal_and_service_units(tmp_path):
    u,job,cfg,state,calls=fixture(tmp_path);before=cfg.read_bytes()
    sentinel=tmp_path/'nfs-unit';sentinel.write_text('RequiresMountsFor=/mnt/eval-share')
    u.apply(job)
    assert (u.app/'worker/client.py').read_text()=='# new worker'
    assert json.loads((u.app/'.worker-release.json').read_text())['sha256']==job['release']['id']
    assert cfg.read_bytes()==before and state.read_text()=='null' and sentinel.read_text()=='RequiresMountsFor=/mnt/eval-share'
    assert calls==['stop','start'] and not u.journal.exists()
    assert (u.app.parent/('.mini-eval-backup-'+job['id'])/'worker/client.py').read_text()=='# old worker'


def test_failure_restores_old_code_before_failed_ack(tmp_path):
    u,job,cfg,state,calls=fixture(tmp_path);u.api.fail_success=True
    u.apply(job)
    assert u.api.status=='FAILED' and (u.app/'worker/client.py').read_text()=='# old worker'
    assert calls==['stop','start','stop','start'] and not u.journal.exists()


def test_lost_success_ack_never_rolls_back_admitted_node(tmp_path):
    u,job,cfg,state,calls=fixture(tmp_path);u.api.success_ack_lost=True
    u.apply(job)
    assert u.api.status=='SUCCEEDED' and calls==['stop','start']
    assert (u.app/'worker/client.py').read_text()=='# new worker'


def test_remaining_journal_prevents_swap(tmp_path):
    u,job,cfg,state,calls=fixture(tmp_path);state.write_text('{"task":"unfinished"}')
    u.apply(job)
    assert (u.app/'worker/client.py').read_text()=='# old worker'
    assert u.api.status=='FAILED' and u.journal.exists()
    assert json.loads(u.journal.read_text())['error']=='rollback_failed'
    assert state.read_text()=='{"task":"unfinished"}'


def test_interrupted_swap_recovers_backup(tmp_path):
    u,job,cfg,state,calls=fixture(tmp_path)
    backup=u.app.parent/('.mini-eval-backup-'+job['id']);u.app.rename(backup)
    # Restarted updater accepts the temporarily absent app only with a journal.
    stage=u.app.parent/'stage';stage.mkdir()
    record={'id':job['id'],'phase':'swapping','stage':str(stage),'backup':str(backup),'status':None}
    atomic(u.journal,record)
    recovered=Updater(u.config);recovered.api=u.api;recovered.control=u.control
    recovered.step()
    assert (u.app/'worker/client.py').read_text()=='# old worker' and u.api.status=='FAILED'
    assert calls==['stop','start'] and not u.journal.exists()


def test_omitted_gpu_config_and_local_lock_prevent_code_replacement(tmp_path):
    import fcntl
    u,job,cfg,state,calls=fixture(tmp_path)
    omitted=cfg.parent/'gpu1.json';value=json.loads(cfg.read_text());value.update(gpu_index=1,state_path=str(tmp_path/'other-state.json'));omitted.write_text(json.dumps(value))
    with pytest.raises(ValueError,match='unlisted'):
        from contextlib import ExitStack
        with ExitStack() as stack:u.idle_locks(stack,[0])
    omitted.unlink()
    with open(str(state)+'.lock','a') as held:
        fcntl.flock(held,fcntl.LOCK_EX|fcntl.LOCK_NB)
        with pytest.raises(BlockingIOError):
            with ExitStack() as stack:u.idle_locks(stack,[0])
    assert (u.app/'worker/client.py').read_text()=='# old worker'


def test_corrupt_archive_never_stops_old_workers(tmp_path):
    u,job,cfg,state,calls=fixture(tmp_path);job['release']['id']='0'*64
    u.apply(job)
    assert calls==[] and u.api.status=='FAILED'
    assert (u.app/'worker/client.py').read_text()=='# old worker'
    assert not list(u.app.parent.glob('.mini-eval-stage-*'))
