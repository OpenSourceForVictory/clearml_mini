"""Real service entrypoints and retained logs after cancellation/container removal."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from uuid import uuid4

import pytest
from backend.tests.test_process_log_failures import fixture as prepared
from runner.process_log_store import locked, read_json
from worker.process_logs import LogService


@pytest.mark.parametrize('mode', ['host', 'docker'])
@pytest.mark.parametrize('outcome', ['success', 'nonzero', 'missing', 'cancel'])
def test_service_entry_capture_lifecycle(tmp_path, mode, outcome):
    image = os.getenv('PROCESS_LOG_TEST_IMAGE')
    if mode == 'docker' and not image:
        pytest.skip('set PROCESS_LOG_TEST_IMAGE for real Docker entrypoint checks')
    worker, task, logs, registry_path, item, _ = prepared(tmp_path)
    output = tmp_path / 'output'
    output.mkdir()
    work = tmp_path / 'work'
    work.mkdir()
    script = tmp_path / 'service.py'
    script.write_text("import os,sys,time\n"
                      "assert os.environ['MINI_EVAL_PORT']=='19000'\n"
                      "assert os.environ['SERVICE_VALUE']=='passed'\n"
                      "assert sys.argv[1:]==['--task-name','same service']\n"
                      "print('service stdout',flush=True)\n"
                      "print('service stderr',file=sys.stderr,flush=True)\n"
                      + ('time.sleep(30)\n' if outcome == 'cancel' else '')
                      + ('sys.exit(7)\n' if outcome == 'nonzero' else ''))
    command = [sys.executable if mode == 'host' else 'python3', str(script), '--task-name', 'same service']
    if outcome == 'missing':
        command = ['/missing-business-program']
    template = {'execution_mode': mode, 'command': command}
    if mode == 'docker':
        template['runtime'] = {'work_dir': str(work), 'output_dir': str(output),
                               'environment': {'SERVICE_VALUE': 'passed'}}
    config = dict(workload='service', port=19000, bind_host='127.0.0.1', template=template,
                  _resolved_command=command, _resolved_environment={'SERVICE_VALUE': 'passed'},
                  _resolved_cwd=str(work), _process_logs=logs)
    input_path = tmp_path / 'input.json'
    input_path.write_text(json.dumps(config))
    environment = {**os.environ, 'PYTHONPATH': str(Path.cwd()), 'MINI_EVAL_INPUT': str(input_path),
                   'MINI_EVAL_WORK_DIR': str(work), 'MINI_EVAL_OUTPUT_DIR': str(output)}
    name = 'mini-eval-log-entry-' + uuid4().hex
    args = [sys.executable, '-m', 'runner.service_entry']
    if mode == 'docker':
        args = ['docker', 'run', '--name', name, '--network', 'none', '--user', f'{os.getuid()}:{os.getgid()}',
                '--entrypoint', 'python3', '--workdir', '/adapter',
                '--mount', f'type=bind,source={tmp_path},target={tmp_path}',
                '--mount', f'type=bind,source={Path.cwd() / "runner"},target=/adapter/runner,readonly']
        for key, value in {**environment, 'PYTHONPATH': '/adapter'}.items():
            if key in ('PYTHONPATH', 'MINI_EVAL_INPUT', 'MINI_EVAL_WORK_DIR', 'MINI_EVAL_OUTPUT_DIR'):
                args += ['-e', key + '=' + value]
        args += [image, '-m', 'runner.service_entry']
    process = subprocess.Popen(args, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True)
    try:
        if outcome == 'cancel':
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline:
                with locked(logs['root'], exclusive=False, wait=True) as descriptor:
                    entries = read_json(descriptor, 'manifest.json')['scopes'].values()
                    captured = any(row['scope'] == 'service' and row['next_sequence'] >= 2 for row in entries)
                if captured:
                    break
                assert process.poll() is None, process.communicate()
                time.sleep(0.05)
            else:
                pytest.fail('service output was not durably captured before cancellation')
            if mode == 'docker':
                subprocess.run(['docker', 'stop', '--time', '1', name], check=True, capture_output=True)
            else:
                os.killpg(process.pid, signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=20)
        if outcome == 'success':
            assert process.returncode == 0, stderr.decode()
        elif outcome == 'nonzero':
            assert process.returncode == 7, stderr.decode()
        else:
            assert process.returncode != 0
        if mode == 'docker':
            subprocess.run(['docker', 'rm', name], check=True, capture_output=True)
        # Simulate the actual Worker observation after the process/container is
        # gone; terminal synchronization must retain output and mark interruption.
        worker.children.clear()
        service = LogService(worker)
        service.sync(registry_path, item)
        status, page = service.read(item, dict(scope='service', episode_id=0,
                                               generation=logs['generation'], query={}))
        assert status == 'ready'
        text = ''.join(row['text'] for row in page['records'])
        if outcome != 'missing':
            assert 'service stdout' in text and 'service stderr' in text
        with locked(logs['root'], exclusive=False) as descriptor:
            entries = read_json(descriptor, 'manifest.json')['scopes'].values()
            row = next(row for row in entries if row['scope'] == 'service')
        assert row['status'] == ('incomplete' if outcome in ('cancel', 'missing') else 'closed')
        assert page['closed']
    finally:
        if mode == 'docker':
            subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
        elif process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.communicate(timeout=10)
