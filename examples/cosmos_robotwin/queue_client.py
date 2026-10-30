"""Real RoboTwin scene checks for scheduler validation; no inference/model evaluation."""
import json
import subprocess
import sys
from runner.sdk import EvaluationClient, EpisodeResult

TASKS = {'adjust_bottle', 'beat_block_hammer', 'click_bell'}
CAMERAS = {'head_camera', 'left_camera', 'right_camera'}


def parse_result(text, task_name, seed):
    for line in reversed(text.splitlines()):
        try:
            report = json.loads(line)
        except ValueError:
            continue
        if not isinstance(report, dict) or report.get('kind') != 'environment-check':
            continue
        if (report.get('task') != task_name or report.get('seed') != seed
                or report.get('model_evaluation') is not False):
            raise RuntimeError('unexpected simulator check identity')
        cameras = report.get('cameras', {})
        if set(cameras) != CAMERAS or any(
            not isinstance(shape, list) or len(shape) != 3 or shape[2] != 3
            or any(type(n) is not int or n <= 0 for n in shape) for shape in cameras.values()
        ):
            raise RuntimeError('invalid camera check result')
        return report
    raise RuntimeError('simulator did not produce a verified scene result')


class RobotwinEnvironmentClient(EvaluationClient):
    def setup(self, context):
        if context.config['task_name'] not in TASKS:
            raise ValueError('unsupported environment check task')
        params = context.config['parameters']
        if set(params) - {'max_seed_attempts'}:
            raise ValueError('unknown environment check parameter')
        self.attempts = params.get('max_seed_attempts', 3)
        if type(self.attempts) is not int or not 1 <= self.attempts <= 3:
            raise ValueError('max_seed_attempts must be 1–3')
        self.next_seed = context.config['seed']
        context.work_dir.mkdir(parents=True, exist_ok=True)
        context.report('RoboTwin 场景初始化自检；不连接推理服务，不统计模型成功率')

    def run_episode(self, task, episode, context):
        skipped = []
        for attempt in range(self.attempts):
            seed = max(episode.seed, self.next_seed)
            self.next_seed = seed + 1
            log_path = context.work_dir / f'environment-{episode.id}-seed-{seed}.log'
            context.report(f'{task["task_name"]}: initializing seed={seed}')
            with log_path.open('w') as log:
                result = subprocess.run(
                    [sys.executable, '-m', 'examples.cosmos_robotwin.smoke_environment',
                     '--task', task['task_name'], '--seed', str(seed)],
                    stdout=log, stderr=subprocess.STDOUT, timeout=240,
                )
            with log_path.open('rb') as log:
                log.seek(max(0, log_path.stat().st_size - 65536))
                text = log.read().decode('utf-8', errors='replace')
            if result.returncode:
                if 'envs.utils.create_actor.UnStableError:' in text:
                    skipped.append(seed)
                    continue
                raise RuntimeError(f'simulator failed ({result.returncode}); log: {log_path.name}\n{text[-2000:]}')
            report = parse_result(text, task['task_name'], seed)
            report.update(requested_seed=episode.seed, skipped_seeds=skipped,
                          inference_endpoint_used=False, simulator_log=log_path.name)
            name = f'environment-episode-{episode.id}.json'
            (context.output_dir / name).write_text(json.dumps(report, ensure_ascii=False, indent=2))
            return EpisodeResult(success=True, metrics={
                'environment_check': 1, 'model_evaluation': 0,
                'actual_seed': seed, 'skipped_seeds': len(skipped), 'camera_count': 3,
            }, artifacts=[name])
        raise RuntimeError(f'all {self.attempts} scene seeds unstable: {skipped}')
