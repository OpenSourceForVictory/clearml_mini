import io
import json
import subprocess
import tarfile
import threading
import uuid
from http.server import ThreadingHTTPServer
import pytest
from runner.cs_demo import evaluate
from runner.cs_contract import endpoint_parts
from scripts.cs_test_service import Handler
from worker.client import Worker


def test_demo_really_calls_service_and_handles_error():
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        config = {'endpoint': f'http://127.0.0.1:{server.server_port}/infer', 'seed': 7, 'episodes': 3,
                  'task_name': 'demo', 'parameters': {}, 'request_timeout_seconds': 1}
        metrics, results = evaluate(config)
        assert metrics['episode_count'] == 3 and metrics['success_rate'] == 1
        assert all(r['steps'] >= 1 for r in results)
        with pytest.raises(RuntimeError, match='inference request failed'):
            evaluate({**config, 'task_name': 'fail'})
        with pytest.raises(RuntimeError, match='timed out'):
            evaluate({**config, 'task_name': 'slow'})
    finally:
        server.shutdown();server.server_close();thread.join(timeout=2)


def test_cs_container_command_and_input_are_not_shell_interpolated(tmp_path, monkeypatch):
    calls, copied = [], []
    worker = Worker({'runner_image': 'legacy:1', 'client_images': ['approved:cs']}, tmp_path/'state')
    worker.slot_id = 'slot'
    task = {'execution_id': str(uuid.uuid4()), 'job_id': 'job', 'benchmark': 'cs-client', 'image': 'approved:cs',
        'gpu_uuid': None, 'cpus': 1, 'ram_gb': 1, 'timeout_seconds': 20, 'episodes': 3,
        'client_spec': {'contract_version': 1, 'template': {'image': 'approved:cs', 'command': ['python', '-m', 'runner.cs_demo']},
                        'endpoint': 'http://192.168.137.223:19090/infer', 'task_name': '$(touch /tmp/no)',
                        'seed': 3, 'parameters': {}, 'request_timeout_seconds': 5}}
    def docker(*args, **kwargs):
        calls.append(args)
        if args[0]=='cp':
            from pathlib import Path
            copied.append(json.loads(Path(args[1]).read_text()))
    monkeypatch.setattr('worker.client.docker', docker)
    worker.create(task)
    assert calls[0][calls[0].index('--network')+1] == 'bridge'
    assert '--volume' not in calls[0] and '--privileged' not in calls[0]
    assert 'runner.cs_entry' in calls[0] and task['client_spec']['task_name'] not in calls[0]
    assert copied[0]['episodes'] == 3 and copied[0]['task_name'] == '$(touch /tmp/no)'
    with pytest.raises(RuntimeError, match='not approved'):
        worker.create({**task, 'image': 'unapproved:1'})


@pytest.mark.parametrize('name,kind,size', [('mini-eval-output/../secret','file',1), ('mini-eval-output/link','link',1),
                                          ('mini-eval-output/huge','file',9*1024*1024)])
def test_unsafe_artifact_archive_rejected(monkeypatch,name,kind,size):
    from worker.artifacts import read_artifacts
    stream=io.BytesIO()
    with tarfile.open(fileobj=stream,mode='w') as archive:
        info=tarfile.TarInfo(name)
        if kind=='link':
            info.type=tarfile.SYMTYPE;info.linkname='/etc/shadow';archive.addfile(info)
        else:
            info.size=size;archive.addfile(info,io.BytesIO(b'x'*size))
    class Process:
        stdout=io.BytesIO(stream.getvalue());stderr=io.BytesIO()
        def poll(self):return 0
        def wait(self,**kwargs):return 0
        def kill(self):pass
    monkeypatch.setattr('worker.artifacts.subprocess.Popen',lambda *a,**kw:Process())
    with pytest.raises(RuntimeError):read_artifacts('container')


def test_supervisor_enforces_total_timeout():
    import sys
    result=subprocess.run([sys.executable,'-m','runner.supervise','--timeout','1',sys.executable,'-c','import time;time.sleep(30)'],
                          capture_output=True,text=True,timeout=5)
    assert result.returncode==124 and 'execution deadline exceeded' in result.stderr
