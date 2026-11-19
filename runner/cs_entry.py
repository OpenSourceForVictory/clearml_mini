"""Container entrypoint for the versioned client evaluation contract."""
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import sys
import time
from .cs_contract import VERSION, INPUT_PATH, OUTPUT_DIR, check_ip, endpoint_parts


def preflight(endpoint, timeout):
    parts, port = endpoint_parts(endpoint)
    # This executes inside the bounded supervisor process, including DNS resolution.
    addresses = socket.getaddrinfo(parts.hostname, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise RuntimeError('inference service DNS lookup returned no address')
    for _, _, _, _, address in addresses:
        check_ip(address[0])
    deadline = time.monotonic()+timeout
    for family, kind, proto, _, address in addresses:
        try:
            with socket.socket(family, kind, proto) as sock:
                sock.settimeout(max(.1, deadline-time.monotonic()))
                sock.connect(address)
                if parts.scheme in ('https', 'wss'):
                    with ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname):
                        pass
            return
        except OSError:
            if time.monotonic() >= deadline:
                break
    raise RuntimeError('inference service connection check failed or timed out')


def main():
    try:
        input_path = os.environ.get('MINI_EVAL_INPUT', INPUT_PATH)
        output_dir = os.environ.get('MINI_EVAL_OUTPUT_DIR', OUTPUT_DIR)
        config = json.loads(Path(input_path).read_text())
        if config['contract_version'] != VERSION:
            raise ValueError('unsupported client contract version')
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        if config.get('service_mode')!='none':
            print(json.dumps({'stage': 'connecting', 'task_name': config['task_name']}), flush=True)
            preflight(config['endpoint'], config['request_timeout_seconds'])
            print(json.dumps({'stage': 'connected'}), flush=True)
        from .task_input import publication_path
        input_path=publication_path(config,input_path)
        env = {**os.environ, 'MINI_EVAL_INPUT': input_path, 'MINI_EVAL_OUTPUT_DIR': output_dir}
        command, cwd = config['template']['command'], None
        if config['template'].get('runtime') is not None:
            from .image_runtime import resolve
            command, additions, cwd = resolve(config,input_path=input_path)
            env.update(additions)
        if config['template'].get('execution_mode') == 'host':
            command = config.get('_resolved_command', command)
            env.update(config.get('_resolved_environment', {}))
            cwd = config.get('_resolved_cwd', env.get('MINI_EVAL_WORK_DIR'))
        from .task_input import build, bind_platform
        config=build(config,env)
        # Publish the normalized input before the user's program starts. Keep
        # legacy fields so existing SDK clients continue reading the same file.
        Path(input_path).write_text(json.dumps(config,ensure_ascii=False,allow_nan=False))
        host_runtime=config['template'].get('host_runtime') or {}
        if (config['template'].get('execution_mode')=='host'
                and host_runtime.get('episode_launch')=='per_episode'
                and config['template'].get('integration_mode')!='sdk-command'):
            # Bind against each child input, never against the outer task count.
            from .host_episode import main as run_host_episodes
            run_host_episodes(config,command,cwd,env)
            return
        schema = config['template'].get('parameter_schema')
        if schema is not None and config['template'].get('integration_mode') != 'sdk-command':
            from .parameter_contract import bind_values
            command, additions = bind_values(schema, config.get('parameters', {}), command)
            if set(additions) & set(config.get('environment', {})):
                raise ValueError('business environment conflicts with runtime overrides')
            env.update(additions)
        if config['template'].get('integration_mode') != 'sdk-command':
            command, additions=bind_platform(config['template'].get('platform_bindings',[]),config,command)
            if set(additions)&set(config.get('environment',{})):
                raise ValueError('platform environment conflicts with runtime overrides')
            env.update(additions)
        if config['template'].get('integration_mode') == 'sdk-command':
            command = [sys.executable, '-m', 'runner.command_sdk']
        from .process_command import for_template
        command=for_template(config['template'],command,cwd,env)
        # Keep the SDK wrapper on the controlled platform environment; it
        # applies the business environment only to per-episode subprocesses.
        launch_env=(dict(os.environ) if config['template'].get('execution_mode')=='host'
                    and config['template'].get('integration_mode')=='sdk-command' else env)
        launch_env.update(MINI_EVAL_INPUT=input_path,MINI_EVAL_OUTPUT_DIR=output_dir)
        if config.get('_process_logs') and config['template'].get('integration_mode')!='sdk-command':
            from .process_log_run import run as captured_run
            code=captured_run(command,config=config,scope='business',env=launch_env,cwd=cwd)
        else:
            code = subprocess.call(command, env=launch_env, cwd=cwd)
        raise SystemExit(code)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        # Never print response payloads or authentication data from the remote service.
        print('CS client execution or result processing failed: '+str(exc), file=sys.stderr, flush=True)
        raise SystemExit(2)


if __name__ == '__main__':
    main()
