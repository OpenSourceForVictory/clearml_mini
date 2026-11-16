"""Offline extracted-bundle test. Uses temporary paths, no platform or devices."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from runner.episode_contract import validate_event
from runner.task_input import build

ROOT = Path(__file__).resolve().parent

def main():
    with tempfile.TemporaryDirectory(prefix='mini-eval-sdk-') as tmp:
        env = dict(os.environ, MINI_EVAL_INPUT=tmp+'/input.json',
                   MINI_EVAL_WORK_DIR=tmp, MINI_EVAL_OUTPUT_DIR=tmp+'/output')
        Path(env['MINI_EVAL_OUTPUT_DIR']).mkdir()
        commands = [([sys.executable, '-m', 'runner.sdk', 'sdk_client:Client'], True),
                    ([sys.executable, '-m', 'runner.command_sdk'], True),
                    ([sys.executable, str(ROOT/'direct_client.py')], True),
                    ([sys.executable, str(ROOT/'ordinary_command.py')], False)]
        for failed in (False, True):
            config = build({'task_name': 'demo', 'case_id': 'demo-1', 'episodes': 2,
                'seed': 42, 'endpoint': '', 'parameters': {'force_failure': failed},
                'template': {'command': [sys.executable, str(ROOT/'command_episode.py')]}}, env)
            Path(env['MINI_EVAL_INPUT']).write_text(json.dumps(config))
            for command, reports in commands:
                result = subprocess.run(command, cwd=ROOT, env=env, text=True,
                                        capture_output=True, timeout=15, check=True)
                events = [json.loads(line.removeprefix('MINI_EVAL_EVENT '))
                          for line in result.stdout.splitlines() if line.startswith('MINI_EVAL_EVENT ')]
                for event in events:
                    validate_event(event)
                finals = [json.loads(line)['final'] for line in result.stdout.splitlines()
                          if line.startswith('{') and 'final' in json.loads(line)]
                if reports:
                    assert [e['status'] for e in events] == ['STARTED', 'COMPLETED'] * 2
                    assert [e['seed'] for e in events] == [42, 42, 43, 43]
                    assert len(finals) == 1 and finals[0]['episode_count'] == 2
                    assert finals[0]['success_rate'] == (0 if failed else 1)
                else:
                    assert not events and not finals
                    assert (Path(env['MINI_EVAL_OUTPUT_DIR'])/'command-result.json').is_file()
        print('PASS: ordinary command, SDK class, SDK command, direct protocol; success and business-failure cases.')

if __name__ == '__main__':
    main()
