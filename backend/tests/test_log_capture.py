import json
import os
from pathlib import Path
import sys
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
