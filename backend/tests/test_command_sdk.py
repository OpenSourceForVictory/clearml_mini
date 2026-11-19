import json,os,subprocess,sys
from pathlib import Path
import pytest

@pytest.mark.parametrize('behavior',['success','false','missing','invalid','exit'])
def test_command_sdk_reports_only_real_business_results(tmp_path,behavior):
    work=tmp_path/'work';work.mkdir();output=tmp_path/'output';output.mkdir()
    script=tmp_path/'business.py';script.write_text("import os,json,sys\nfrom pathlib import Path\n"+{
        'success':"Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({'success':True,'metrics':{'seed':int(os.environ['MINI_EVAL_EPISODE_SEED'])}}))",
        'false':"Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({'success':False}))",
        'missing':'pass','invalid':"Path(os.environ['MINI_EVAL_RESULT']).write_text('{\"success\":1}')",'exit':'sys.exit(3)'}[behavior])
    config={'task_name':'generic','episodes':2,'seed':7,'endpoint':'http://127.0.0.1:9000','template':{'command':[sys.executable,str(script),'${episode_id}']}}
    inp=tmp_path/'input.json';inp.write_text(json.dumps(config))
    result=subprocess.run([sys.executable,'-m','runner.command_sdk'],env={**os.environ,'PYTHONPATH':str(Path.cwd()),'MINI_EVAL_INPUT':str(inp),'MINI_EVAL_WORK_DIR':str(work),'MINI_EVAL_OUTPUT_DIR':str(output)},capture_output=True,text=True)
    events=[json.loads(x) for x in (work/'episodes.jsonl').read_text().splitlines()]
    if behavior in ['success','false']:
        assert result.returncode==0,result.stderr
        assert [e['status'] for e in events]==['STARTED','COMPLETED','STARTED','COMPLETED']
        metrics=json.loads((output/'report.json').read_text())['metrics'];assert metrics['episode_count']==2
        assert metrics['success_rate']==int(behavior=='success')
    else:
        assert result.returncode!=0 and events[-1]['status']=='ERROR'
        assert not (output/'report.json').exists()
        if behavior in ('missing','invalid'):
            from runner.episode_contract import result_error_code
            assert result_error_code(events[-1]['error'])==('missing_result_file' if behavior=='missing' else 'invalid_result_file')
            assert events[-1]['exit_code']==0
            assert 'Traceback' not in result.stderr
