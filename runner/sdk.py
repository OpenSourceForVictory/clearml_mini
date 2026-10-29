"""Simulation SDK: users implement one episode; the framework owns reporting."""
from dataclasses import dataclass, field
import importlib
import json
import os
from pathlib import Path
import sys
import time

EVENT_PREFIX = 'MINI_EVAL_EVENT '


@dataclass(frozen=True)
class Episode:
    id: int
    seed: int


@dataclass
class EpisodeResult:
    success: bool
    metrics: dict = field(default_factory=dict)
    artifacts: list[str] = field(default_factory=list)


class EvaluationClient:
    def setup(self, context):
        pass

    def run_episode(self, task, episode, context):
        raise NotImplementedError

    def teardown(self):
        pass


@dataclass
class Context:
    config: dict
    output_dir: Path

    @property
    def work_dir(self):
        return Path(os.environ.get('MINI_EVAL_WORK_DIR', str(self.output_dir.parent/'work')))

    @property
    def assets_dir(self):
        return Path(os.environ.get('MINI_EVAL_ASSETS_DIR', '/opt/mini-eval/assets'))

    @property
    def cache_dir(self):
        return Path(os.environ.get('MINI_EVAL_CACHE_DIR', '/opt/mini-eval/cache'))

    def report(self, stage):
        print(json.dumps({'stage': str(stage)[:200]}), flush=True)


def episode_seed(seed, episode_id):
    return (seed + episode_id - 1) % 2147483648


def emit(event):
    from .episode_contract import validate_event
    validate_event(event)
    encoded = json.dumps(event, allow_nan=False, separators=(',', ':'))
    if os.environ.get('MINI_EVAL_WORK_DIR'):
        with (Path(os.environ['MINI_EVAL_WORK_DIR'])/'episodes.jsonl').open('a') as f:
            f.write(encoded+'\n'); f.flush(); os.fsync(f.fileno())
    print(EVENT_PREFIX + encoded, flush=True)


def run(client, config, output_dir=None):
    context = Context(config, Path(output_dir or os.environ.get('MINI_EVAL_OUTPUT_DIR', '/tmp/mini-eval-output')))
    context.output_dir.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    successes = 0
    try:
        client.setup(context)
        for i in range(1, config['episodes'] + 1):
            episode = Episode(i, episode_seed(config['seed'], i))
            base = {'episode_id': i, 'seed': episode.seed}
            emit({**base, 'status': 'STARTED'})
            begin = time.monotonic()
            try:
                result = client.run_episode(config, episode, context)
                if not isinstance(result, EpisodeResult):
                    raise TypeError('run_episode must return EpisodeResult')
                emit({**base, 'status': 'COMPLETED', 'success': result.success,
                      'metrics': result.metrics, 'artifacts': result.artifacts,
                      'runtime': time.monotonic()-begin})
                successes += int(result.success)
            except Exception:
                # User exception details go to stderr; progress does not expose remote payloads.
                emit({**base, 'status': 'ERROR', 'error': 'simulation episode raised an exception'})
                raise
    finally:
        client.teardown()
    metrics = {'success_rate': successes/config['episodes'], 'episode_count': config['episodes'],
               'runtime': time.monotonic()-started}
    print(json.dumps({'final': metrics}), flush=True)
    return metrics


def main():
    if len(sys.argv) != 2 or ':' not in sys.argv[1]:
        raise SystemExit('usage: python -m runner.sdk module:ClientClass')
    module, name = sys.argv[1].split(':', 1)
    client = getattr(importlib.import_module(module), name)()
    if not isinstance(client, EvaluationClient):
        raise TypeError('client must inherit EvaluationClient')
    run(client, json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text()))


if __name__ == '__main__':
    # Use the canonical module's classes when invoked with python -m.
    from runner.sdk import main as sdk_main
    sdk_main()
