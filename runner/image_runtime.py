"""Bounded, shell-free configuration for user-supplied Python images."""
import re
from pathlib import PurePosixPath

WORK = '/tmp/mini-eval-work'
OUTPUT = '/tmp/mini-eval-output'
ADAPTER = '/tmp/mini-eval-runtime'
NAME = re.compile(r'^[A-Za-z][A-Za-z0-9_]{0,63}$')
ENV = re.compile(r'^[A-Za-z_][A-Za-z0-9_-]{0,127}$')
TOKEN = re.compile(r'\$\{([^}]+)\}')


def environment(value):
    if not isinstance(value, dict) or len(value) > 64:
        raise ValueError('environment must contain at most 64 variables')
    for key, text in value.items():
        if not isinstance(key,str) or not ENV.fullmatch(key) or key.startswith(('MINI_EVAL_', 'NVIDIA_', 'ASCEND_')) or key in ('CUDA_VISIBLE_DEVICES', 'ROCR_VISIBLE_DEVICES', 'HIP_VISIBLE_DEVICES', 'PYTHONPATH', 'PYTHONHOME'):
            raise ValueError('invalid or reserved environment variable: ' + str(key))
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


def placeholders(text, assets, mounts=()):
    allowed = {'task_name', 'episodes', 'seed', 'endpoint', 'input_path', 'work_dir', 'output_dir', 'episode_id', 'episode_seed', 'result_path', 'port', 'health_port', 'bind_host'} | {'assets:'+k for k in assets} | {'mounts:'+k for k in mounts}
    if any(key not in allowed for key in TOKEN.findall(text)) or '${' in TOKEN.sub('', text):
        raise ValueError('unknown or malformed runtime placeholder')


def validate(value, command=()):
    if not isinstance(value, dict) or set(value) - {'interpreter', 'environment', 'overridable_env', 'work_dir', 'output_dir', 'assets', 'mounts', 'command_mode', 'working_directory'}:
        raise ValueError('invalid runtime fields')
    result = {'interpreter': 'python3', 'environment': {}, 'overridable_env': [], 'work_dir': WORK, 'output_dir': OUTPUT, 'assets': {}, **value}
    interpreter=result['interpreter']
    if not isinstance(interpreter,str) or not interpreter or len(interpreter)>1000 or any(c in interpreter for c in ('\x00','\n','\r')) or (interpreter not in ('python','python3') and not PurePosixPath(interpreter).is_absolute()):
        raise ValueError('platform interpreter must be python, python3 or an absolute Python path')
    if result.get('command_mode','legacy') not in ('legacy','exec'):raise ValueError('invalid command_mode')
    directory=result.get('working_directory')
    if directory is not None and (not isinstance(directory,str) or not directory or len(directory)>2000 or not PurePosixPath(directory).is_absolute() or '..' in PurePosixPath(directory).parts or any(c in directory for c in ('\x00','\n','\r')) or '${' in directory):
        raise ValueError('container working_directory must be an absolute directory')
    environment(result['environment'])
    keys = result['overridable_env']
    if not isinstance(keys, list) or len(keys) > 64 or any(not isinstance(k, str) for k in keys):
        raise ValueError('invalid overridable_env')
    environment(dict.fromkeys(keys, ''))
    assets = result['assets']
    if not isinstance(assets, dict) or len(assets) > 16 or any(not NAME.fullmatch(k) for k in assets):
        raise ValueError('invalid logical asset names')
    from .mount_contract import mounts
    if 'mounts' in result: result['mounts'] = mounts(result['mounts'])
    paths = [target(result['work_dir']), target(result['output_dir']), *[target(p) for p in assets.values()], *[target(m['target']) for m in result.get('mounts',{}).values()]]
    for i, path in enumerate(paths):
        if any(overlaps(path, other) for other in paths[:i]):
            raise ValueError('container directories must not overlap')
        for fixed in (WORK, OUTPUT):
            if overlaps(path, fixed) and not (i == (0 if fixed == WORK else 1) and path == fixed):
                raise ValueError('container directory overlaps platform execution directory')
    for text in [*command, *result['environment'].values()]:
        placeholders(text, assets, result.get('mounts',{}))
    return result


def resolve(config, input_path=None):
    runtime = validate(config['template']['runtime'], config['template']['command'])
    overrides = environment(config.get('environment', {}))
    if set(overrides) - set(runtime['overridable_env']):
        raise ValueError('environment override is not allowed')
    values = {k: str(config.get(k,'')) for k in ('task_name', 'episodes', 'seed', 'endpoint')}
    values.update({k:str(config[k]) for k in ('port','health_port','bind_host') if k in config})
    values.update(input_path=input_path or '/tmp/mini-eval-job.json', work_dir=runtime['work_dir'], output_dir=runtime['output_dir'])
    values.update({'assets:'+k: v for k, v in runtime['assets'].items()})
    values.update({'mounts:'+k: m['target'] for k,m in runtime.get('mounts',{}).items()})
    def expand(text):
        placeholders(text, runtime['assets'], runtime.get('mounts',{}))
        return TOKEN.sub(lambda m: values.get(m[1], m[0]), text)
    env = {k: expand(v) for k, v in {**runtime['environment'], **overrides}.items()}
    env.update(MINI_EVAL_INPUT=values['input_path'], MINI_EVAL_WORK_DIR=runtime['work_dir'], MINI_EVAL_OUTPUT_DIR=runtime['output_dir'],
               MINI_EVAL_TASK_NAME=values['task_name'], MINI_EVAL_EPISODES=values['episodes'], MINI_EVAL_SEED=values['seed'], MINI_EVAL_ENDPOINT=values['endpoint'])
    return [expand(x) for x in config['template']['command']], env, runtime.get('working_directory',runtime['work_dir'])
