import json
import errno
import os
from pathlib import Path
import subprocess
import sys
import pytest
from runner.log_capture import Ring, read_ring, capture, SLOTS, BLOCK, ProtocolCapture


def test_ring_rolls_and_limits_disk(tmp_path):
    ring=Ring(tmp_path)
    for i in range(SLOTS+10):ring.write('stderr','\x01'*BLOCK)
    row,cursor=read_ring(tmp_path,0)
    assert row['gap'] and row['stream']=='stderr' and cursor==11
    assert sum(p.stat().st_size for p in tmp_path.iterdir()) < 4*1024*1024
    assert len(list(tmp_path.iterdir()))==SLOTS+2
    ring.write('stderr','最终错误')
    row,_=read_ring(tmp_path,ring.next-1)
    assert row['content']=='最终错误'
    restarted=Ring(tmp_path)
    row,_=read_ring(tmp_path,restarted.next-1)
    assert row['gap'] and 'restarted' in row['content']


def test_capture_preserves_final_and_events_after_large_output(tmp_path):
    code="""import sys,json
sys.stdout.write('x'*1000000+'\\n');sys.stdout.flush()
print('MINI_EVAL_EVENT '+json.dumps({'type':'done'}),flush=True)
print(json.dumps({'final':{'success_rate':1,'episode_count':1,'runtime':1}}),flush=True)
sys.stderr.write('last failure 中文\\n')
sys.exit(3)
"""
    assert capture([sys.executable,'-c',code],tmp_path)==3
    assert json.loads((tmp_path/'exit.json').read_text())['code']==3
    assert json.loads((tmp_path/'final.json').read_text())['final']['success_rate']==1
    assert json.loads((tmp_path/'events.jsonl').read_text())['type']=='done'
    output=[];cursor=0
    while True:
        row,cursor=read_ring(tmp_path/'logs',cursor)
        if row is None:break
        output.append(row)
    assert output[0]['gap']
    assert any('last failure 中文' in x['content'] and x['stream']=='stderr' for x in output)


def test_protocol_partial_utf8_and_long_line(tmp_path):
    parser=ProtocolCapture(tmp_path)
    parser.feed(b'x'*70000)
    parser.feed(b'{"final":{"bad":true}}\n')
    assert not (tmp_path/'final.json').exists()
    data=json.dumps({'final':{'label':'中文'}},ensure_ascii=False).encode()+b'\n'
    for byte in data:parser.feed(bytes([byte]))
    assert json.loads((tmp_path/'final.json').read_text())['final']['label']=='中文'
    parser.events=32*1024*1024
    import pytest
    with pytest.raises(RuntimeError,match='journal limit'):
        parser.feed(b'MINI_EVAL_EVENT {"type":"done"}\n')


def test_container_capture_retains_stderr_without_diagnostic_ring(tmp_path):
    assert capture([sys.executable,'-c',"import sys;sys.stderr.write('x'*10000+'最终错误')"],tmp_path,ring=False)==0
    value=json.loads((tmp_path/'stderr-tail.json').read_text())['content']
    assert value.endswith('最终错误') and len(value.encode())<=7168
    assert not (tmp_path/'logs').exists()


@pytest.mark.parametrize('source', ['diagnostic_ring', 'protocol_events', 'protocol_budget',
                                     'container_stderr', 'exit_record'])
def test_disk_full_capture_drains_business_stdout_and_keeps_child_exit(tmp_path, monkeypatch, source):
    from runner import log_capture
    marker = tmp_path/'business-finished'
    code = ("import pathlib,sys\n"
            "sys.stdout.write('x'*1000000+'\\n');sys.stdout.flush()\n"
            "sys.stderr.write('diagnostic tail\\n');sys.stderr.flush()\n"
            f"pathlib.Path({str(marker)!r}).write_text('done')\n")

    def full(*args, **kwargs):
        raise OSError(errno.ENOSPC, 'injected full disk')

    if source == 'diagnostic_ring':
        monkeypatch.setattr(log_capture.Ring, 'write', full)
    elif source == 'protocol_events':
        monkeypatch.setattr(log_capture.ProtocolCapture, 'feed', full)
    elif source == 'protocol_budget':
        def budget(*args):
            raise RuntimeError('injected journal limit')
        monkeypatch.setattr(log_capture.ProtocolCapture, 'feed', budget)
    else:
        original = log_capture.atomic
        failing_name = 'stderr-tail.json' if source == 'container_stderr' else 'exit.json'
        def file_full(path, value):
            if Path(path).name == failing_name:
                full()
            return original(path, value)
        monkeypatch.setattr(log_capture, 'atomic', file_full)

    assert capture([sys.executable, '-c', code], tmp_path,
                   ring=source != 'container_stderr') == 0
    assert marker.read_text() == 'done'
    if source == 'exit_record':
        assert not (tmp_path/'exit.json').exists()
    else:
        assert json.loads((tmp_path/'exit.json').read_text())['code'] == 0


@pytest.mark.skipif(os.getenv('RECOVERY_RUNTIME_TEST') != '1',
                    reason='real Docker tmpfs and current Runner opt-in')
def test_real_docker_full_protocol_tmpfs_does_not_kill_business(tmp_path):
    repo = Path(__file__).resolve().parents[2]
    output = tmp_path/'output'
    output.mkdir()
    business = ('import pathlib\n'
                'for _ in range(3000):\n'
                ' print(\'MINI_EVAL_EVENT {"episode_id":1,"status":"STARTED"}\')\n'
                'pathlib.Path("/output/done").write_text("complete")\n')
    result = subprocess.run([
        'docker', 'run', '--rm', '--network', 'none', '--workdir', '/code',
        '--tmpfs', '/tmp/capture:rw,size=65536',
        '--mount', f'type=bind,source={repo / "runner"},target=/code/runner,readonly',
        '--mount', f'type=bind,source={output},target=/output',
        '--env', 'PYTHONPATH=/code', '--entrypoint', 'python',
        os.getenv('RECOVERY_RUNTIME_IMAGE', 'mini-eval-api:recovery-foundations-20261125'),
        '-m', 'runner.log_capture', '/tmp/capture', 'python', '-c', business,
    ], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr[-1000:]
    assert 'protocol capture unavailable: OSError' in result.stderr
    assert (output/'done').read_text() == 'complete'
