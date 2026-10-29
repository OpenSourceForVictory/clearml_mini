import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from sqlalchemy import select
from backend.tests.test_execution import env, node
from backend.tests.test_cs_protocol import template, submit_body
from backend.app.core import ClientTemplate, Job, Run, Slot
from backend.app.scheduling import tick, transition
from examples.cosmos_robotwin.client import CosmosRobotwinClient, asset_path
from runner.sdk import Context, Episode


def test_endpoint_exclusivity_across_users_and_cleanup(env, monkeypatch):
    main,c,auth=env
    monkeypatch.setenv('MAX_ACTIVE_GLOBAL','4');monkeypatch.setenv('MAX_ACTIVE_PER_USER','4')
    t=template(env);_,slots=node(env,count=2)
    with main.SessionLocal() as s:
        s.get(ClientTemplate,t).endpoint_exclusive=True
        for sid in slots:
            slot=s.get(Slot,sid);slot.protocol_version=3;slot.client_images=['approved:cs']
        s.commit()
    for owner in ('alice','bob'):
        r=c.post('/api/v1/jobs',headers=auth[owner],json=submit_body(t));assert r.status_code==201
    assert tick(main.SessionLocal)==1
    with main.SessionLocal() as s:
        r=s.scalar(select(Run).where(Run.status=='RESERVED'))
        transition(s,r,'FAILED');r.cleanup_confirmed=False;s.commit()
    assert tick(main.SessionLocal)==0
    with main.SessionLocal() as s:
        r=s.scalar(select(Run).where(Run.cleanup_confirmed.is_(False)))
        r.cleanup_confirmed=True;s.commit()
    assert tick(main.SessionLocal)==1


def client_context(tmp_path,monkeypatch):
    cosmos=tmp_path/'cosmos'; entry=cosmos/'examples/action_pretrain/robotwin_client.py'
    entry.parent.mkdir(parents=True);entry.write_text('# fixture')
    robotwin=tmp_path/'RoboTwin';(robotwin/'scripts').mkdir(parents=True)
    (robotwin/'scripts/eval_policy_xpolicylab.py').write_text('# fixture')
    assets=tmp_path/'assets';(assets/'cosmos').mkdir(parents=True)
    from examples.cosmos_robotwin.assets import ASSET_REVISION, ASSETS
    version = assets/'robotwin'/ASSET_REVISION
    for name in ('embodiments', 'objects', 'background_texture'):
        (version/name).mkdir(parents=True)
    (version/'manifest.json').write_text(json.dumps({'revision': ASSET_REVISION, 'sha256': ASSETS}))
    (assets/'cosmos/eef.json').write_text('{"eef_pose_frame":"world"}')
    work=tmp_path/'work';work.mkdir();output=tmp_path/'output';output.mkdir()
    monkeypatch.setenv('COSMOS_ROOT',str(cosmos));monkeypatch.setenv('ROBOTWIN_ROOT',str(robotwin))
    monkeypatch.setenv('MINI_EVAL_ASSETS_DIR',str(assets));monkeypatch.setenv('MINI_EVAL_WORK_DIR',str(work))
    config={'task_name':'adjust_bottle','parameters':{'eef_config':'cosmos/eef.json','max_steps':32},
            'seed':100000,'endpoint':'ws://192.168.137.223:8000','request_timeout_seconds':10}
    return Context(config,output)


def test_adapter_single_episode_actual_seed_and_summary(tmp_path,monkeypatch):
    context=client_context(tmp_path,monkeypatch)
    client=CosmosRobotwinClient();client.setup(context)
    seeds=[]
    def execute(args,**kwargs):
        assert '--episodes' in args and args[args.index('--episodes')+1]=='1'
        seed=int(args[args.index('--seed')+1]);seeds.append(seed)
        path=Path(args[args.index('--output')+1])/'adjust_bottle/demo_clean/joint/metrics.json'
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps({'completed_episodes':1,'episodes':[{'status':'finished','seed':seed+1,'success':False,'executed_steps':32,'seconds':1.5}],
                                   'skipped_seeds':[{'seed':seed}]}))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('examples.cosmos_robotwin.client.subprocess.run',execute)
    first=client.run_episode(context.config,Episode(1,100000),context)
    second=client.run_episode(context.config,Episode(2,100001),context)
    assert seeds==[100000,100002] and not first.success and not second.success
    assert first.metrics['actual_seed']==100001
    report=json.loads((context.output_dir/'sample-report.json').read_text())
    assert len(report['episodes'])==2 and report['episodes'][1]['plan_seed']==100001


def test_invalid_paths_combinations_and_missing_results(tmp_path,monkeypatch):
    context=client_context(tmp_path,monkeypatch);client=CosmosRobotwinClient();client.setup(context)
    with pytest.raises(ValueError):asset_path(context.assets_dir,'../../escape.json')
    with pytest.raises(ValueError):client.command({**context.config,'task_name':'a,b'},0,tmp_path)
    monkeypatch.setattr('examples.cosmos_robotwin.client.subprocess.run',lambda *a,**kw:SimpleNamespace(returncode=0))
    with pytest.raises(RuntimeError,match='missing'):client.run_episode(context.config,Episode(1,100000),context)


def test_sample_manifest_and_template_match_contract():
    from backend.app.batches import Case
    from backend.app.clients import TemplateCreate
    root=Path('examples/cosmos_robotwin')
    cases=[Case.model_validate(c) for c in json.loads((root/'suite.json').read_text())]
    assert len({c.case_id for c in cases})==len(cases)==2
    template=TemplateCreate.model_validate_json((root/'template.json').read_text())
    assert template.episode_reporting and template.endpoint_exclusive and template.protocols==['ws','wss']


def test_invocation_applies_connection_and_receive_timeouts(monkeypatch,tmp_path):
    import sys,types
    from examples.cosmos_robotwin import invoke
    seen={}
    class Connection:
        def recv(self,*args,**kwargs): seen['recv']=kwargs;return b'metadata'
        def close(self): seen['closed']=True
    client=types.ModuleType('websockets.sync.client')
    def connect(*args,**kwargs): seen['connect']=kwargs;return Connection()
    client.connect=connect
    sync=types.ModuleType('websockets.sync');sync.client=client
    ws=types.ModuleType('websockets');ws.sync=sync
    for key,value in [('websockets',ws),('websockets.sync',sync),('websockets.sync.client',client)]:monkeypatch.setitem(sys.modules,key,value)
    def execute(path,run_name):
        connection=client.connect('ws://service:8000',compression=None)
        assert connection.recv()==b'metadata';connection.close()
    monkeypatch.setattr(invoke.runpy,'run_path',execute)
    monkeypatch.setenv('COSMOS_REQUEST_TIMEOUT','17')
    monkeypatch.setattr(sys,'argv',['invoke',str(tmp_path/'client.py'),'--episodes','1'])
    invoke.main()
    assert seen['connect']['open_timeout']==17 and seen['connect']['close_timeout']==5
    assert seen['recv']['timeout']==17 and seen['closed']


def test_official_asset_extraction_checks_hash_and_paths(tmp_path):
    import hashlib
    import zipfile
    from examples.cosmos_robotwin.assets import extract_verified
    archive = tmp_path / 'asset.zip'
    target = tmp_path / 'assets'
    target.mkdir()
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('objects/example.json', '{}')
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match='checksum'):
        extract_verified(archive, target, '0' * 64)
    assert not list(target.iterdir())
    extract_verified(archive, target, checksum)
    assert (target / 'objects/example.json').read_text() == '{}'
    with zipfile.ZipFile(archive, 'w') as output:
        output.writestr('../outside.txt', 'unsafe')
    checksum = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(RuntimeError, match='unsafe path'):
        extract_verified(archive, target, checksum)
    assert not (tmp_path / 'outside.txt').exists()


def test_asset_version_is_published_only_after_complete_validation(tmp_path, monkeypatch):
    from examples.cosmos_robotwin import assets
    cache = tmp_path / 'cache'
    cache.mkdir()
    target = tmp_path / 'assets'
    for name in assets.ASSETS:
        (cache / name).write_text('fake-download')
    def extract(archive, staging, expected):
        directory = staging / archive.stem
        directory.mkdir(exist_ok=True)
        if archive.stem == 'embodiments':
            (directory / 'config_tmp.yml').write_text('root: ${ASSETS_PATH}/assets/embodiments')
        if archive.stem == 'objects':
            raise RuntimeError('interrupted extraction')
    monkeypatch.setattr(assets, 'extract_verified', extract)
    with pytest.raises(RuntimeError, match='interrupted'):
        assets.install(target, cache, 'https://unused.example')
    published = target / 'robotwin' / assets.ASSET_REVISION
    assert not published.exists()
    def finish(archive, staging, expected):
        (staging / archive.stem).mkdir(exist_ok=True)
    monkeypatch.setattr(assets, 'extract_verified', finish)
    assets.install(target, cache, 'https://unused.example')
    assets.validate(published)
    assert (published / 'embodiments/config.yml').read_text() == 'root: /opt/RoboTwin/assets/embodiments'
    assert not (published.parent / ('.' + assets.ASSET_REVISION + '.partial')).exists()
    # The published version is immutable and does not trigger extraction again.
    monkeypatch.setattr(assets, 'extract_verified', lambda *a: pytest.fail('unexpected re-extraction'))
    assets.install(target, cache, 'https://unused.example')


def test_client_rejects_unpublished_assets(tmp_path, monkeypatch):
    from examples.cosmos_robotwin.assets import ASSET_REVISION
    context = client_context(tmp_path, monkeypatch)
    (context.assets_dir / 'robotwin' / ASSET_REVISION / 'manifest.json').unlink()
    with pytest.raises(RuntimeError, match='assets are not ready'):
        CosmosRobotwinClient().setup(context)
