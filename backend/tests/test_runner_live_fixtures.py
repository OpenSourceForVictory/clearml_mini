"""Real local HTTP and subprocess fixtures; these are not trained model evaluations."""
import http.client
import json
import sys
import threading
from types import SimpleNamespace
from http.server import ThreadingHTTPServer

import pytest
from runner import cs_demo,inference_demo,mock_eval,supervise
from runner.cs_entry import preflight
from runner.sdk_demo import DemoClient


@pytest.fixture
def server():
    http=ThreadingHTTPServer(('127.0.0.1',0),inference_demo.Handler)
    thread=threading.Thread(target=http.serve_forever,daemon=True);thread.start()
    yield http.server_address
    http.shutdown();http.server_close();thread.join(2)


def test_real_http_client_and_sdk_episode(server,tmp_path,monkeypatch,capsys):
    host,port=server;endpoint=f'http://{host}:{port}'
    with pytest.raises(ValueError,match="external inference service"):
        preflight(endpoint,2)
    config={'endpoint':endpoint,'seed':7,'episodes':3,'task_name':'position','parameters':{},'request_timeout_seconds':2}
    input_path=tmp_path/'input.json';input_path.write_text(json.dumps(config))
    monkeypatch.setenv('MINI_EVAL_INPUT',str(input_path));monkeypatch.setenv('MINI_EVAL_OUTPUT_DIR',str(tmp_path/'out'))
    cs_demo.main()
    report=json.loads((tmp_path/'out/report.json').read_text())
    assert report['metrics']['success_rate']==1 and len(report['episodes'])==3
    assert json.loads(capsys.readouterr().out.splitlines()[-1])['final']['episode_count']==3
    demo=DemoClient();context=SimpleNamespace(output_dir=tmp_path/'out')
    demo.setup(context);result=demo.run_episode(config,SimpleNamespace(seed=9),context)
    assert result.success and result.metrics['steps'] in (1,2,3)


@pytest.mark.parametrize('method,path,body,status',[
    ('GET','/healthz',None,200),('GET','/missing',None,404),
    ('POST','/',b'',413),('POST','/',b'x'*65537,413),
    ('POST','/',b'bad',400),('POST','/',b'{}',400),
    ('POST','/',b'{"observation":{"position":0,"target":1}}',200)])
def test_http_fixture_rejects_invalid_requests(server,method,path,body,status):
    connection=http.client.HTTPConnection(*server,timeout=2)
    try:
        connection.request(method,path,body);response=connection.getresponse()
        assert response.status==status
        if status==200:
            value=json.loads(response.read());assert value==({'status':'ok','fixture':True} if method=='GET' else {'action':1})
    finally:connection.close()


@pytest.mark.parametrize('code',[0,7])
def test_supervisor_preserves_real_process_exit(monkeypatch,code):
    monkeypatch.setattr(sys,'argv',['supervise','--timeout','2',sys.executable,'-c',f'raise SystemExit({code})'])
    with pytest.raises(SystemExit) as exc:supervise.main()
    assert exc.value.code==code


def test_supervisor_kills_deadline_process(monkeypatch,capsys):
    monkeypatch.setattr(sys,'argv',['supervise','--timeout','0',sys.executable,'-c','import time;time.sleep(60)'])
    with pytest.raises(SystemExit) as exc:supervise.main()
    assert exc.value.code==124 and 'deadline exceeded' in capsys.readouterr().err


@pytest.mark.parametrize('mode',['success','failure','invalid'])
def test_mock_cli_never_emits_success_for_failure(monkeypatch,capsys,mode):
    monkeypatch.setattr(sys,'argv',['mock','--job-id','test','--model-ref','test','--episodes','0' if mode=='invalid' else '2'])
    monkeypatch.setenv('MOCK_EPISODE_SLEEP','0');monkeypatch.setenv('MOCK_FAIL','1' if mode=='failure' else '0')
    if mode=='success':mock_eval.main()
    else:
        with pytest.raises(SystemExit if mode=='invalid' else RuntimeError):mock_eval.main()
    lines=capsys.readouterr().out.splitlines()
    assert any('final' in json.loads(line) for line in lines)==(mode=='success')
