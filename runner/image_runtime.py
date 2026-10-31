"""Bounded, shell-free configuration for user-supplied Python images."""
import re
from pathlib import PurePosixPath

WORK = '/tmp/mini-eval-work'
OUTPUT = '/tmp/mini-eval-output'
ADAPTER = '/tmp/mini-eval-runtime'
NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
ENV = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,127}$')
TOKEN = re.compile(r'\$\{([^}]+)\}')


def environment(value):
    if not isinstance(value, dict) or len(value) > 64:
        raise ValueError('environment must contain at most 64 variables')
    for key, text in value.items():
        if not ENV.fullmatch(key) or key.startswith(('MINI_EVAL_', 'NVIDIA_')) or key in ('CUDA_VISIBLE_DEVICES', 'PYTHONPATH', 'PYTHONHOME'):
            raise ValueError('invalid or reserved environment variable: ' + key)
        if not isinstance(text, str) or len(text) > 4096 or '\x00' in text:
            raise ValueError('environment values must be strings of at most 4096 characters')
    if sum(len(k)+len(v) for k, v in value.items()) > 16384:
        raise ValueError('environment exceeds 16 KiB')
    return value


def overlaps(a, b):
    a, b = PurePosixPath(a), PurePosixPath(b)
    return a == b or a in b.parents or b in a.parents


def target(value):
    if not isinstance(value, str):
        raise ValueError('container directory must be a string')
    p = PurePosixPath(value)
    if not p.is_absolute() or str(p) != value or '..' in p.parts or ',' in value or len(value) > 1000 or any(ord(c) < 32 for c in value):
        raise ValueError('invalid container directory')
    for reserved in ('/proc', '/sys', '/dev', '/etc', '/bin', '/sbin', '/lib', '/lib64', '/usr', '/run', '/root', '/opt/mini-eval', ADAPTER, '/tmp/mini-eval-job.json'):
        if overlaps(value, reserved):
            raise ValueError('container directory overlaps a reserved path')
    return value


def placeholders(text, assets):
    allowed = {'task_name', 'episodes', 'seed', 'endpoint', 'input_path', 'work_dir', 'output_dir'} | {'assets:'+k for k in assets}
    if any(key not in allowed for key in TOKEN.findall(text)) or '${' in TOKEN.sub('', text):
        raise ValueError('unknown or malformed runtime placeholder')


def validate(value, command=()):
    if not isinstance(value, dict) or set(value) - {'interpreter', 'environment', 'overridable_env', 'work_dir', 'output_dir', 'assets'}:
        raise ValueError('invalid runtime fields')
    result = {'interpreter': 'python3', 'environment': {}, 'overridable_env': [], 'work_dir': WORK, 'output_dir': OUTPUT, 'assets': {}, **value}
    if result['interpreter'] not in ('python', 'python3'):
        raise ValueError('interpreter must be python or python3')
    environment(result['environment'])
    keys = result['overridable_env']
    if not isinstance(keys, list) or len(keys) > 64 or any(not isinstance(k, str) for k in keys):
        raise ValueError('invalid overridable_env')
    environment(dict.fromkeys(keys, ''))
    assets = result['assets']
    if not isinstance(assets, dict) or len(assets) > 16 or any(not NAME.fullmatch(k) for k in assets):
        raise ValueError('invalid logical asset names')
    paths = [target(result['work_dir']), target(result['output_dir']), *[target(p) for p in assets.values()]]
    for i, path in enumerate(paths):
        if any(overlaps(path, other) for other in paths[:i]):
            raise ValueError('container directories must not overlap')
        for fixed in (WORK, OUTPUT):
            if overlaps(path, fixed) and not (i == (0 if fixed == WORK else 1) and path == fixed):
                raise ValueError('container directory overlaps platform execution directory')
    for text in [*command, *result['environment'].values()]:
        placeholders(text, assets)
    return result


def resolve(config):
    runtime = validate(config['template']['runtime'], config['template']['command'])
    overrides = environment(config.get('environment', {}))
    if set(overrides) - set(runtime['overridable_env']):
        raise ValueError('environment override is not allowed')
    values = {k: str(config[k]) for k in ('task_name', 'episodes', 'seed', 'endpoint')}
    values.update(input_path='/tmp/mini-eval-job.json', work_dir=runtime['work_dir'], output_dir=runtime['output_dir'])
    values.update({'assets:'+k: v for k, v in runtime['assets'].items()})
    def expand(text):
        placeholders(text, runtime['assets'])
        return TOKEN.sub(lambda m: values[m[1]], text)
    env = {k: expand(v) for k, v in {**runtime['environment'], **overrides}.items()}
    env.update(MINI_EVAL_INPUT=values['input_path'], MINI_EVAL_WORK_DIR=runtime['work_dir'], MINI_EVAL_OUTPUT_DIR=runtime['output_dir'],
               MINI_EVAL_TASK_NAME=values['task_name'], MINI_EVAL_EPISODES=values['episodes'], MINI_EVAL_SEED=values['seed'], MINI_EVAL_ENDPOINT=values['endpoint'])
    return [expand(x) for x in config['template']['command']], env, runtime['work_dir']
