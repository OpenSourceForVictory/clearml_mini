"""Platform SDK adapter for the pinned Cosmos RoboTwin client."""
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit
from runner.sdk import EvaluationClient, EpisodeResult

UPSTREAM_COMMIT = '2ba852c1ff3ce923405bd47a65074488a3358781'


def single_name(value, name):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value):
        raise ValueError(name+' must be one task/config name, not a combination')
    return value


def asset_path(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError('eef_config must be relative to the worker assets directory')
    target = (root/relative).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise ValueError('eef_config is missing or outside the assets directory')
    return target


class CosmosRobotwinClient(EvaluationClient):
    def setup(self, context):
        self.cosmos = Path(os.environ.get('COSMOS_ROOT', '/opt/cosmos-framework'))
        self.robotwin = Path(os.environ.get('ROBOTWIN_ROOT', '/opt/RoboTwin'))
        self.entry = self.cosmos/'examples/action_pretrain/robotwin_client.py'
        if not self.entry.is_file():
            raise FileNotFoundError('Cosmos client entrypoint is missing: '+str(self.entry))
        if not (self.robotwin/'scripts/eval_policy_xpolicylab.py').is_file():
            raise FileNotFoundError('RoboTwin scripts/eval_policy_xpolicylab.py is required')
        params = context.config['parameters']
        allowed = {'task_config','control_mode','state_source','eef_config','max_steps','instruction','instruction_type','video_fps'}
        if set(params)-allowed:
            raise ValueError('unknown Cosmos client parameters: '+','.join(sorted(set(params)-allowed)))
        self.eef = asset_path(context.assets_dir, params.get('eef_config'))
        if urlsplit(context.config['endpoint']).scheme not in ('ws','wss'):
            raise ValueError('Cosmos inference requires a ws:// or wss:// endpoint')
        self.next_seed = context.config['seed']
        self.records = []

    def command(self, task, seed, output):
        p = task['parameters']
        name = single_name(task['task_name'], 'task_name')
        config = single_name(p.get('task_config', 'demo_clean'), 'task_config')
        mode = p.get('control_mode', 'joint'); state = p.get('state_source', 'command')
        if mode not in ('joint','eef') or state not in ('command','measured'):
            raise ValueError('invalid control_mode or state_source')
        args = [sys.executable, '-m', 'examples.cosmos_robotwin.invoke', str(self.entry),
                '--robotwin-root', str(self.robotwin), '--server-uri', task['endpoint'],
                '--task-name', name, '--task-config', config, '--control-mode', mode,
                '--state-source', state, '--seed', str(seed), '--episodes', '1',
                '--eef-config', str(self.eef), '--render-freq', '0', '--output', str(output)]
        for key in ('max_steps','video_fps'):
            if key in p:
                if type(p[key]) is not int or not 1 <= p[key] <= 100000:
                    raise ValueError('invalid '+key)
                args.extend(['--'+key.replace('_','-'),str(p[key])])
        for key in ('instruction','instruction_type'):
            if key in p:
                if not isinstance(p[key],str) or not p[key].strip() or len(p[key]) > 2000:
                    raise ValueError('invalid '+key)
                if key == 'instruction_type' and p[key] not in ('seen','unseen'):
                    raise ValueError('invalid instruction_type')
                args.append('--'+key.replace('_','-')+'='+p[key])
        return args, output/name/config/mode/'metrics.json'

    def run_episode(self, task, episode, context):
        requested_seed = self.next_seed
        output = context.work_dir/f'episode-{episode.id:06d}'
        # Never silently reuse cached upper-level outputs on a repeated SDK invocation.
        output.mkdir(parents=True, exist_ok=False)
        command, metrics_path = self.command(task, requested_seed, output)
        env = {**os.environ, 'COSMOS_REQUEST_TIMEOUT': str(task['request_timeout_seconds'])}
        context.report('cosmos-robotwin-rollout')
        result = subprocess.run(command, env=env, check=False)
        if result.returncode:
            raise RuntimeError(f'Cosmos RoboTwin client exited with code {result.returncode}; see execution logs')
        if not metrics_path.is_file() or metrics_path.stat().st_size > 8*1024*1024:
            raise RuntimeError('missing or oversized Cosmos metrics.json')
        summary = json.loads(metrics_path.read_text())
        completed = [r for r in summary.get('episodes',[]) if r.get('status') == 'finished']
        if len(completed) != 1 or summary.get('completed_episodes') != 1:
            raise RuntimeError('Cosmos client did not finish exactly one evaluation episode')
        record = completed[0]
        actual_seed = record.get('seed')
        if type(actual_seed) is not int or actual_seed < requested_seed or type(record.get('success')) is not bool:
            raise RuntimeError('invalid Cosmos seed or success result')
        metrics = {'actual_seed': actual_seed, 'requested_seed': requested_seed,
                   'skipped_seeds': len(summary.get('skipped_seeds',[])),
                   'executed_steps': record.get('executed_steps'), 'policy_seconds': record.get('seconds')}
        if any(type(v) not in (int,float) or not math.isfinite(v) or v < 0 for v in metrics.values()):
            raise RuntimeError('invalid Cosmos episode metrics')
        self.next_seed = actual_seed+1
        self.records.append({'episode_id': episode.id, 'plan_seed': episode.seed, 'success': record['success'],
                             'metrics': metrics, 'local_output': str(output)})
        report = {'kind':'cosmos-robotwin-closed-loop', 'upstream_commit':UPSTREAM_COMMIT,
                  'episodes':self.records, 'note':'Videos and trajectories remain in the worker execution work directory.'}
        encoded = json.dumps(report, ensure_ascii=False, indent=2)
        if len(encoded.encode()) > 8*1024*1024:
            raise RuntimeError('Cosmos report exceeds platform artifact limit')
        (context.output_dir/'sample-report.json').write_text(encoded)
        return EpisodeResult(success=record['success'], metrics=metrics, artifacts=['sample-report.json'])
