"""Executable business samples for recovery tests; no accelerator/model claims."""
import copy
import json
import os
from pathlib import Path


def configure(task, mode, variant):
    integration, launch = variant
    task['episodes'] = 3
    spec = task['client_spec']
    spec['service_mode'] = 'none'
    template = spec['template']
    template.update(execution_mode=mode,
                    task_type='command' if integration in ('command', 'sdk-task-result') else 'evaluation',
                    integration_mode=('direct' if integration == 'command' else
                                      integration if integration in ('sdk-command', 'sdk-task-result') else 'direct-protocol'))
    template['host_runtime']['episode_launch'] = launch


def program(root, variant):
    integration, launch = variant
    header = f'''import json, os, time
from pathlib import Path
config = json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
with Path({str(root / 'invocations.jsonl')!r}).open('a') as record:
    record.write(json.dumps({{'episodes': config['episodes'], 'seed': config['seed'],
                            'episode': os.environ.get('MINI_EVAL_EPISODE_ID')}}) + '\\n')
'''
    if integration == 'sdk-command':
        return header + '''time.sleep(.8)
Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({'success': True}))
'''
    if integration == 'command':
        return header + f'time.sleep({.8 if launch == "per_episode" else 2.4})\n'
    if integration == 'sdk-task-result':
        return header + '''from runner.sdk import report_task_result
time.sleep(2.4)
report_task_result(True, {'score': 1})
'''
    if integration == 'sdk':
        return header + '''from runner.sdk import EvaluationClient, EpisodeResult, run
class Business(EvaluationClient):
    def run_episode(self, task, episode, context):
        time.sleep(.8)
        return EpisodeResult(True)
run(Business(), config)
'''
    return header + '''for i in range(1, config['episodes'] + 1):
    event = {'episode_id': i, 'seed': config['seed'] + i - 1}
    print('MINI_EVAL_EVENT ' + json.dumps({**event, 'status': 'STARTED'}), flush=True)
    time.sleep(.8)
    print('MINI_EVAL_EVENT ' + json.dumps({**event, 'status': 'COMPLETED', 'success': True,
          'metrics': {}, 'artifacts': [], 'runtime': .8}), flush=True)
print(json.dumps({'final': {'success_rate': 1., 'episode_count': config['episodes'], 'runtime': 2.4}}), flush=True)
'''


def container_args(worker, task, root):
    from worker.workspace import execution_dirs
    directory = execution_dirs(worker.workspace, task['execution_id'])
    for name in ('work', 'output'):
        (directory / name).mkdir(parents=True, exist_ok=True)
    config = copy.deepcopy(task['client_spec'])
    config.update(episodes=task['episodes'])
    config['template']['command'] = ['python', str(root / 'business.py')]
    path = root / 'container-input.json'
    path.write_text(json.dumps(config))
    repo = Path(__file__).resolve().parents[2]
    return ['--mount', f'type=bind,source={root},target={root}',
            '--mount', f'type=bind,source={repo},target={repo},readonly',
            '--env', f'PYTHONPATH={repo}', '--env', f'MINI_EVAL_INPUT={path}',
            '--env', f'MINI_EVAL_WORK_DIR={directory / "work"}',
            '--env', f'MINI_EVAL_OUTPUT_DIR={directory / "output"}',
            '--entrypoint', 'python',
            os.getenv('RECOVERY_RUNTIME_IMAGE', 'mini-eval-api:recovery-foundations-20261125'),
            '-m', 'runner.cs_entry']


def verify(db, root, variant):
    from backend.app.core import EpisodeRecord, Run
    integration, launch = variant
    calls = [json.loads(line) for line in (root / 'invocations.jsonl').read_text().splitlines()]
    per_process = launch == 'per_episode' or integration == 'sdk-command'
    assert len(calls) == (3 if per_process else 1), calls
    assert [value['episodes'] for value in calls] == ([1] * 3 if launch == 'per_episode' else [3] * len(calls))
    if launch == 'per_episode':
        assert [value['seed'] for value in calls] == [0, 1, 2]
        assert [value['episode'] for value in calls] == ['1', '2', '3']
    for i in range(1, 4):
        record = db.get(EpisodeRecord, ('r', i))
        if integration in ('command', 'sdk-task-result') and launch != 'per_episode':
            assert record is None
        else:
            assert record and record.result['success'] is True
            assert record.result['seed'] == i - 1
    if integration == 'sdk-task-result':
        assert db.get(Run, 'r').result_json == {'kind': 'task', 'success': True, 'metrics': {'score': 1}}
