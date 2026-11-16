"""Validate distributed examples against API contracts and a real HTTP service."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.request import urlopen

import pytest
from backend.app.clients import TemplateCreate
from backend.app.templates import ServerCreate

ROOT = Path(__file__).resolve().parents[2]
EXAMPLES = ROOT / 'examples/runtime_templates'
CATALOG = ROOT / 'frontend/public/template-examples/catalog.json'


def test_all_six_templates_and_downloads_match_contract():
    examples = json.loads(CATALOG.read_text())
    assert len(examples) == 6
    assert len({e['id'] for e in examples}) == 6
    for example in examples:
        schema = ServerCreate if example['kind'] == 'server' else TemplateCreate
        schema.model_validate(example['definition'])
    for name in ('client.py', 'episode.py', 'server.py', 'Dockerfile', 'README.md'):
        assert (EXAMPLES/name).read_bytes() == (CATALOG.parent/name).read_bytes()


@pytest.mark.parametrize('mode', ['sdk-command', 'direct'])
def test_example_client_against_real_echo_service(tmp_path, mode):
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        port = s.getsockname()[1]
    server_input = tmp_path/'server.json'
    server_input.write_text(json.dumps({'business': {'model': 'echo-demo'}, 'platform': {'listen': {'address': '127.0.0.1', 'port': port}}}))
    env = {**os.environ, 'MINI_EVAL_INPUT': str(server_input), 'PYTHONPATH': str(ROOT)}
    server = subprocess.Popen([sys.executable, str(EXAMPLES/'server.py')], env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        endpoint = f'http://127.0.0.1:{port}'
        deadline = time.monotonic()+10
        while True:
            try:
                with urlopen(endpoint+'/healthz', timeout=.2) as r:
                    assert r.status == 200
                break
            except OSError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(.05)
        config = {'task_name': 'echo_a', 'episodes': 2, 'seed': 1, 'parameters': {'scene': 'table'}, 'endpoint': endpoint,
                  'input_version': 1, 'business': {'scene': 'table'}, 'task': {'task_name': 'echo_a', 'episodes': 2, 'seed': 1},
                  'platform': {'service': {'endpoint': endpoint}}}
        input_path = tmp_path/'input.json'
        input_path.write_text(json.dumps(config))
        work = tmp_path/'work'; work.mkdir()
        env.update(MINI_EVAL_INPUT=str(input_path), MINI_EVAL_WORK_DIR=str(work), MINI_EVAL_OUTPUT_DIR=str(tmp_path/'output'))
        if mode == 'sdk-command':
            result_path = tmp_path/'result.json'
            for i in (1, 2):
                env.update(MINI_EVAL_RESULT=str(result_path), MINI_EVAL_EPISODE_ID=str(i))
                subprocess.run([sys.executable, str(EXAMPLES/'episode.py')], env=env, check=True, timeout=15, capture_output=True)
                assert json.loads(result_path.read_text())['success'] is True
        else:
            subprocess.run([sys.executable, str(EXAMPLES/'client.py')], env=env, check=True, timeout=15, capture_output=True)
            events = [json.loads(line) for line in (work/'episodes.jsonl').read_text().splitlines()]
            assert sum(e['status'] == 'COMPLETED' for e in events) == 2
    finally:
        server.terminate()
        server.wait(timeout=5)


@pytest.mark.skipif(os.environ.get('RUN_TEMPLATE_DOCKER_TEST') != '1', reason='requires built mini-eval-template-examples:v1 image')
def test_docker_examples_with_isolated_loopback_service():
    import ast
    source = Path(__file__).read_text()
    tree = ast.parse(source)
    function = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'test_example_client_against_real_echo_service')
    program = '''import json, os, socket, subprocess, sys, time, tempfile
from pathlib import Path
from urllib.request import urlopen
ROOT=Path('/opt/mini-eval')
EXAMPLES=Path('/app')
'''+ast.get_source_segment(source, function)+'''
for mode in ('sdk-command','direct'):
    with tempfile.TemporaryDirectory() as directory:
        test_example_client_against_real_echo_service(Path(directory),mode)
print('PASS Docker SDK-command and direct clients with real HTTP echo')
'''
    result = subprocess.run(['docker', 'run', '--rm', '--network', 'none', '--entrypoint', 'python3',
                             'mini-eval-template-examples:v1', '-c', program], capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    assert 'PASS Docker' in result.stdout
