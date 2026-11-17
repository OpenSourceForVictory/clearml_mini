"""Queue exercise persistence and verdicts, without downloading assets or contacting nodes."""
import io
import json
from pathlib import Path
import runpy
from urllib.error import URLError
from urllib.parse import urlsplit

import pytest

ROOT=Path(__file__).resolve().parents[2]


@pytest.mark.parametrize('mode',['new','existing','register-only','resume','failed','transient'])
def test_queue_driver_registration_resume_and_failure(tmp_path,monkeypatch,mode):
    monkeypatch.chdir(tmp_path)
    source=tmp_path/'examples/cosmos_robotwin/suite-queue-check.json';source.parent.mkdir(parents=True);source.write_text('[{"case_id":"fixture","task_name":"fixture","episodes":1}]')
    state_path=tmp_path/'state.json'
    for key,value in {'EVAL_API_URL':'http://127.0.0.1:18090','EVAL_ADMIN_PASSWORD':'fixture-only','QUEUE_STATE_FILE':str(state_path)}.items():monkeypatch.setenv(key,value)
    if mode=='register-only':monkeypatch.setenv('QUEUE_REGISTER_ONLY','1')
    else:monkeypatch.delenv('QUEUE_REGISTER_ONLY',raising=False)
    if mode=='resume':state_path.write_text(json.dumps({'suite_id':'suite','template_id':'template','pool_id':'pool','run_key':'preserved','batches':['prior'],'history':[]}))
    calls=[];tick={'value':0,'lost':False}
    monkeypatch.setattr('time.sleep',lambda _:tick.__setitem__('value',tick['value']+1))
    name='RoboTwin 三任务场景自检（无模型）'
    def request(req,timeout):
        assert urlsplit(req.full_url).netloc=='127.0.0.1:18090'
        path=urlsplit(req.full_url).path.removeprefix('/api/v1/');body=json.loads(req.data) if req.data else None
        calls.append((path,body,req.get_header('Idempotency-key')))
        if path=='auth/login':value={'access_token':'fixture-token'}
        elif path=='client-templates':value=[{'id':'template','name':name,'version':'queue-v1'}] if mode=='existing' else []
        elif path=='admin/client-templates':value={'id':'template'}
        elif path=='suites':value={'id':'suite'} if body else [{'id':'suite','name':name,'version':'1'}] if mode=='existing' else []
        elif path=='pools':value=[{'id':'pool','name':'gpu-pool'}]
        elif path=='batches':value={'id':'batch'+str(sum(p=='batches' for p,_,_ in calls))}
        elif path.startswith('batches/'):
            if mode=='transient' and not tick['lost']:tick['lost']=True;raise URLError('temporary fixture outage')
            value={'cases':[{'job_id':'job-'+path.split('/')[1]}]}
        elif path=='workers':value=[{'id':'slot','node_name':'fixture-node'}]
        elif path.endswith('/episodes'):value={'progress':{'completed':1}}
        elif path.startswith('jobs/'):
            status='RUNNING' if tick['value']==0 else 'FAILED' if mode=='failed' else 'SUCCEEDED'
            value={'id':path.split('/')[1],'status':status,'started_at':'start','finished_at':None if status=='RUNNING' else 'end','attempts':[{'id':'attempt','slot_id':'slot'}]}
        else:raise AssertionError(path)
        return io.BytesIO(json.dumps(value).encode())
    monkeypatch.setattr('urllib.request.urlopen',request)
    if mode in ('failed','register-only'):
        with pytest.raises(SystemExit) as error:runpy.run_path(str(ROOT/'scripts/robotwin_queue_check.py'),run_name='__main__')
        assert ('Validation failed' in str(error.value))==(mode=='failed')
    else:runpy.run_path(str(ROOT/'scripts/robotwin_queue_check.py'),run_name='__main__')
    state=json.loads(state_path.read_text())
    submissions=[(body,key) for path,body,key in calls if path=='batches']
    assert len(submissions)==(0 if mode=='register-only' else 2 if mode=='resume' else 3)
    assert len({key for _,key in submissions})==len(submissions)
    assert all(body['max_concurrent']==2 for body,_ in submissions)
    if mode=='resume':assert state['batches'][0]=='prior' and state['run_key']=='preserved'
    if mode=='existing':assert not any(p=='admin/client-templates' or p=='suites' and b for p,b,_ in calls)
    if mode!='register-only':
        assert len(state['episodes'])==3 and state['history']
        assert all(j['status']==('FAILED' if mode=='failed' else 'SUCCEEDED') for j in state['final_jobs'])
