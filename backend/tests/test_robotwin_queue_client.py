import json
from types import SimpleNamespace
import pytest
from runner.sdk import Context, Episode
from examples.cosmos_robotwin.queue_client import RobotwinEnvironmentClient, parse_result


def report(task='click_bell', seed=100002):
    return dict(kind='environment-check',task=task,seed=seed,model_evaluation=False,
                cameras={k:[240,320,3] for k in ['head_camera','left_camera','right_camera']})


def test_verified_report_required():
    with pytest.raises(RuntimeError):parse_result('{}','click_bell',100002)
    with pytest.raises(RuntimeError):parse_result(json.dumps(report(seed=10)),'click_bell',100002)
    bad=report();bad['cameras']['head_camera']=[0,320,3]
    with pytest.raises(RuntimeError):parse_result(json.dumps(bad),'click_bell',100002)


def test_unstable_seed_retries_and_reports_actual_result(tmp_path,monkeypatch):
    context=Context(dict(task_name='click_bell',seed=100001,parameters={}),tmp_path/'output')
    context.output_dir.mkdir()
    client=RobotwinEnvironmentClient();client.setup(context)
    calls=[]
    def run(argv,stdout,**kw):
        seed=int(argv[-1]);calls.append(seed)
        stdout.write('envs.utils.create_actor.UnStableError: unstable' if seed==100001 else json.dumps(report()))
        return SimpleNamespace(returncode=1 if seed==100001 else 0)
    monkeypatch.setattr('examples.cosmos_robotwin.queue_client.subprocess.run',run)
    result=client.run_episode(context.config,Episode(1,100001),context)
    assert calls==[100001,100002]
    assert result.success and result.metrics['model_evaluation'] == 0
    saved=json.loads((context.output_dir/result.artifacts[0]).read_text())
    assert saved['skipped_seeds']==[100001] and saved['seed']==100002


def test_non_instability_error_is_not_retried(tmp_path,monkeypatch):
    context=Context(dict(task_name='click_bell',seed=100001,parameters={}),tmp_path/'output')
    client=RobotwinEnvironmentClient();client.setup(context)
    def run(argv,stdout,**kw):
        stdout.write('FileNotFoundError: missing asset');return SimpleNamespace(returncode=1)
    monkeypatch.setattr('examples.cosmos_robotwin.queue_client.subprocess.run',run)
    with pytest.raises(RuntimeError,match='missing asset'):
        client.run_episode(context.config,Episode(1,100001),context)


def test_sdk_accepts_environment_metrics(tmp_path, monkeypatch):
    from runner.sdk import run
    def execute(argv,stdout,**kw):
        stdout.write(json.dumps(report(seed=100001)))
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr('examples.cosmos_robotwin.queue_client.subprocess.run',execute)
    result=run(RobotwinEnvironmentClient(),dict(task_name='click_bell',seed=100001,parameters={},episodes=1),tmp_path/'output')
    assert result['episode_count']==1
    assert json.loads((tmp_path/'output/environment-episode-1.json').read_text())['model_evaluation'] is False
