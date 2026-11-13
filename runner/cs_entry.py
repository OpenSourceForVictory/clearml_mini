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
        print(json.dumps({'stage': 'connecting', 'task_name': config['task_name']}), flush=True)
        preflight(config['endpoint'], config['request_timeout_seconds'])
        print(json.dumps({'stage': 'connected'}), flush=True)
        env = {**os.environ, 'MINI_EVAL_INPUT': input_path, 'MINI_EVAL_OUTPUT_DIR': output_dir}
        command, cwd = config['template']['command'], None
        if config['template'].get('runtime') is not None:
            from .image_runtime import resolve
            command, additions, cwd = resolve(config)
            env.update(additions)
        if config['template'].get('execution_mode') == 'host':
            command = config.get('_resolved_command', command)
            env.update(config.get('_resolved_environment', {}))
            cwd = env.get('MINI_EVAL_WORK_DIR')
        if config['template'].get('integration_mode') == 'sdk-command':
            command = [sys.executable, '-m', 'runner.command_sdk']
        code = subprocess.call(command, env=env, cwd=cwd)
        raise SystemExit(code)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        # Never print response payloads or authentication data from the remote service.
        print('CS client startup failed: '+str(exc), file=sys.stderr, flush=True)
        raise SystemExit(2)


if __name__ == '__main__':
    main()
