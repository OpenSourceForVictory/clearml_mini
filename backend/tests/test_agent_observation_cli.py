"""Observer tests use synthetic API/clock, never production credentials or nodes."""
from datetime import datetime,timezone
import io
import json
import sys

import pytest
from scripts import observe_node_agents as observer


@pytest.mark.parametrize('scenario',['healthy','missing','error','refresh'])
def test_bounded_observation_and_credential_redaction(tmp_path,monkeypatch,scenario,capsys):
    output=tmp_path/'observation.jsonl';now=[0.0];requests=[]
    duration=1201 if scenario=='refresh' else 1
    monkeypatch.setattr(sys,'argv',['observe','--api-url','http://test','--node','node-a',
        '--duration',str(duration),'--interval','60','--output',str(output)])
    monkeypatch.setenv('MINI_EVAL_ADMIN_PASSWORD','private-test-password')
    monkeypatch.setattr(observer.time,'monotonic',lambda:now[0])
    monkeypatch.setattr(observer.time,'sleep',lambda seconds:now.__setitem__(0,now[0]+seconds))
    def urlopen(request,timeout):
        requests.append(request);assert timeout==10
        if request.full_url.endswith('auth/login'):
            assert json.loads(request.data)['password']=='private-test-password'
            value={'access_token':'private-test-token'}
        else:
            assert request.get_header('Authorization')=='Bearer private-test-token'
            if scenario=='error':raise RuntimeError('private-test-password private-test-token')
            value=[] if scenario=='missing' else [{'node_name':'node-a','status':'online','held_executions':2,
                'connection':{'agent':{'status':'online','revision':2,'applied_revision':2,'config_status':'applied',
                'seen_at':datetime.now(timezone.utc).isoformat(),'events':['one','two']}}}]
        return io.BytesIO(json.dumps(value).encode())
    monkeypatch.setattr(observer.urllib.request,'urlopen',urlopen)
    observer.main()
    summary=json.loads(output.with_suffix('.summary.json').read_text())
    rows=[json.loads(line) for line in output.read_text().splitlines()]
    assert rows[-1]['elapsed_seconds']==duration
    assert summary['api_errors']==(len(rows) if scenario=='error' else 0)
    node=summary['nodes']['node-a']
    assert node['samples']==(0 if scenario=='error' else len(rows))
    assert node['offline']==(len(rows) if scenario=='missing' else 0)
    assert node['unapplied']==(len(rows) if scenario=='missing' else 0)
    if scenario in ('healthy','refresh'):assert node['max_event_count']==2
    assert len([r for r in requests if r.full_url.endswith('auth/login')])==(2 if scenario=='refresh' else 1)
    public=output.read_text()+output.with_suffix('.summary.json').read_text()+capsys.readouterr().out
    assert 'private-test-password' not in public and 'private-test-token' not in public


@pytest.mark.parametrize('duration,interval',[(0,1),(86401,1),(1,0),(1,61)])
def test_invalid_observation_bounds_rejected(tmp_path,monkeypatch,duration,interval):
    monkeypatch.setattr(sys,'argv',['observe','--api-url','http://test','--node','a','--duration',str(duration),
        '--interval',str(interval),'--output',str(tmp_path/'out')])
    monkeypatch.setattr(observer.urllib.request,'urlopen',lambda *a,**kw:pytest.fail('invalid bounds reached API'))
    with pytest.raises(SystemExit):observer.main()
