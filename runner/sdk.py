"""Simulation SDK: users implement one episode; the framework owns reporting."""
from dataclasses import dataclass, field, replace
import importlib
import json
import os
from pathlib import Path
import sys
import time
import traceback

from .episode_contract import EpisodeResultError, EpisodeExecutionError, validate_event

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
    _episode_output_dir: Path | None = field(default=None, repr=False)

    @property
    def episode_output_dir(self):
        """Current episode directory; unavailable during task setup."""
        if self._episode_output_dir is None:
            raise RuntimeError("episode_output_dir is only available inside run_episode")
        self._episode_output_dir.mkdir(parents=True, exist_ok=True)
        if self._episode_output_dir.is_symlink():
            raise ValueError("episode output directory must not be a symbolic link")
        return self._episode_output_dir

    def artifact_path(self, path):
        """Return the task-root-relative reference expected by EpisodeResult."""
        from .cs_contract import artifact_name
        value = Path(path)
        if not value.is_absolute():
            value = self.output_dir / value
        try:
            root = Path(self.config.get('_artifact_root', self.output_dir))
            name = value.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            raise ValueError("artifact must be inside the task output directory") from None
        if not artifact_name(name):
            raise ValueError("invalid artifact reference")
        return name

    @property
    def input(self):
        from .task_input import build
        return self.config if self.config.get('input_version') == 1 else build(self.config)

    @property
    def business(self):
        return self.input['business']

    @property
    def service_endpoint(self):
        service = self.input['platform'].get('service')
        return service['endpoint'] if service else None

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
    errors = 0
    template = config.get('template', {})
    host = template.get('host_runtime') or {}
    continue_errors = (template.get('execution_mode') == 'host'
                       and host.get('episode_launch') == 'per_episode'
                       and host.get('episode_error_policy') == 'continue')
    try:
        client.setup(context)
        for i in range(1, config['episodes'] + 1):
            episode = Episode(i, episode_seed(config['seed'], i))
            base = {'episode_id': i, 'seed': episode.seed}
            emit({**base, 'status': 'STARTED'})
            client.process_exit_code = None
            begin = time.monotonic()
            try:
                nested = config.get("_parent_episode_id") is not None
                episode_directory = (context.output_dir if nested else Path(config["_artifact_root"])/"episodes"/str(i)/"output"
                                     if config.get("_artifact_layout")=="batch-v1" else context.output_dir / ("episode-" + str(i)))
                episode_context = replace(context, _episode_output_dir=episode_directory)
                result = client.run_episode(config, episode, episode_context)
                if not isinstance(result, EpisodeResult):
                    raise EpisodeResultError('invalid_episode_result')
                event = {**base, 'status': 'COMPLETED', 'success': result.success,
                      'metrics': result.metrics, 'artifacts': result.artifacts,
                      'runtime': time.monotonic()-begin,
                      **({'exit_code':client.process_exit_code} if client.process_exit_code is not None else {})}
                try:validate_event(event)
                except (ValueError, TypeError, OverflowError):raise EpisodeResultError('invalid_episode_result') from None
                emit(event)
                successes += int(result.success)
            except Exception as exc:
                # User exception details go to stderr; progress does not expose remote payloads.
                emit({**base, 'status': 'ERROR', 'error': str(exc) if isinstance(exc, EpisodeResultError) else 'simulation episode raised an exception',
                      **({'exit_code':client.process_exit_code} if client.process_exit_code is not None else {})})
                if isinstance(exc, EpisodeResultError):
                    print(str(exc), file=sys.stderr, flush=True)
                    if not continue_errors:raise EpisodeExecutionError(str(exc)) from None
                else:
                    if not continue_errors:raise
                    traceback.print_exc(file=sys.stderr)
                errors += 1
    finally:
        client.teardown()
    if errors:
        raise EpisodeExecutionError(f'{errors} of {config["episodes"]} business episodes had execution or result errors; remaining episodes were attempted')
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
    try:sdk_main()
    except EpisodeExecutionError as exc:
        print(str(exc), file=sys.stderr, flush=True)
        raise SystemExit(2)
