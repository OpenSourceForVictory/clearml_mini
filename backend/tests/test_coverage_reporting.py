import copy
import json
from types import SimpleNamespace

import pytest
from scripts.coverage_report import DIRECTORIES,FUNCTIONS,summarize,markdown
from scripts.test_coverage import run


def sample():
    names=['backend/app/scheduling.py','backend/app/suites.py','backend/app/clients.py','backend/app/reports.py','worker/client.py','node_agent/client.py','runner/sdk.py','onboarding/ssh.py','scripts/install_worker.py']
    return {'meta':{'branch_coverage':True},'files':{name:{'summary':{'num_statements':10,'covered_lines':10,'num_branches':4,'covered_branches':4},'missing_lines':[],'missing_branches':[]} for name in names}}


def floors():
    return {'directories':{n:{'line':80,'branch':70} for n in DIRECTORIES},'functions':{n:{'line':90,'branch':80} for n in FUNCTIONS}}


def test_weighted_totals_missing_and_overlapping_functions():
    data=sample();data['files']['worker/unexecuted.py']={'summary':{'num_statements':90,'covered_lines':0,'num_branches':16,'covered_branches':0},'missing_lines':[1,2],'missing_branches':[[1,2]]}
    report=summarize(data,floors())
    assert report['total']['lines']==180 and report['total']['covered_lines']==90
    assert report['total']['line_percent']==50.0
    assert report['directories']['worker']['line_percent']==10
    assert report['directories']['worker']['branch_percent']==20
    assert report['missing']['worker/unexecuted.py']['lines']==[1,2]
    assert len(report['violations'])==2
    assert 'worker' in markdown(report)


def test_threshold_boundary_and_missing_sources():
    data=sample();f=floors();assert not summarize(data,f)['violations']
    data['files']['worker/client.py']['summary'].update(covered_lines=9)
    assert not summarize(data,f)['violations']
    data['files']['worker/client.py']['summary'].update(covered_lines=8)
    assert any('业务执行与结果' in v for v in summarize(data,f)['violations']) is False # group includes runner's 10 lines: exactly 90%
    data['files']['worker/client.py']['summary'].update(covered_lines=7)
    assert any('业务执行与结果' in v for v in summarize(data,f)['violations'])
    del data['files']['worker/client.py']
    assert any('worker: source missing' in v for v in summarize(data,f)['violations'])


@pytest.mark.parametrize('kind',['no-branches','absolute-path','missing-floor','bad-floor'])
def test_invalid_measurement_is_not_success(kind):
    data=sample();f=floors()
    if kind=='no-branches':data['meta']['branch_coverage']=False
    if kind=='absolute-path':data['files']['/tmp/foreign.py']=data['files'].pop('worker/client.py')
    if kind=='missing-floor':del f['directories']['runner']
    if kind=='bad-floor':f['directories']['runner']['line']=-1
    with pytest.raises(ValueError):summarize(data,f)


@pytest.mark.parametrize('tests,gate,expected',[(0,0,0),(1,0,1),(0,1,1),(5,1,5)])
def test_runner_preserves_failure_and_produces_reports(tmp_path,monkeypatch,tests,gate,expected):
    calls=[]
    def fake(args,**kwargs):
        calls.append(args)
        code=tests if 'run' in args else gate if 'scripts.coverage_report' in args else 0
        return SimpleNamespace(returncode=code)
    monkeypatch.setattr('scripts.test_coverage.subprocess.run',fake)
    assert run(tmp_path)==expected
    assert json.loads((tmp_path/'test-exit.json').read_text())['pytest_exit_code']==tests
    assert any('html' in args for args in calls) and any('xml' in args for args in calls)
    assert any('scripts.coverage_report' in args for args in calls)


def test_report_cli_writes_human_and_machine_results(tmp_path):
    from scripts.coverage_report import main
    source=tmp_path/'input.json';requirements=tmp_path/'floors.json';out=tmp_path/'report'
    source.write_text(json.dumps(sample()));requirements.write_text(json.dumps(floors()))
    assert main(['--input',str(source),'--floors',str(requirements),'--output',str(out)])==0
    assert json.loads((out/'summary.json').read_text())['total']['line_percent']==100
    assert '功能视图' in (out/'summary.md').read_text()


@pytest.mark.parametrize('failure',['erase','json'])
def test_runner_fails_when_coverage_data_cannot_be_produced(tmp_path,monkeypatch,failure):
    calls=[]
    def fake(args,**kwargs):
        calls.append(args)
        return SimpleNamespace(returncode=2 if failure in args else 0)
    monkeypatch.setattr('scripts.test_coverage.subprocess.run',fake)
    assert run(tmp_path)==2
    assert not any('scripts.coverage_report' in args for args in calls)
    assert any('run' in args for args in calls)==(failure!='erase')


def test_runner_cli_passes_output(monkeypatch):
    from scripts.test_coverage import main
    monkeypatch.setattr('sys.argv',['test_coverage','--output','some-output'])
    monkeypatch.setattr('scripts.test_coverage.run',lambda output: 7 if output=='some-output' else 0)
    assert main()==7
