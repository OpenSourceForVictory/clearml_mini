from backend.tests.test_host_shell_episode import client


def test_host_episode_child_receives_hyphen_environment(tmp_path):
    script=tmp_path/'business.py'
    script.write_text('import os\nassert os.environ["task-config"]=="demo_clean"\nassert os.environ["control-mode"]=="eef"\n')
    result,events,_,_=client(tmp_path,script,ordinary=True,episodes=2,
                            env={'task-config':'demo_clean','control-mode':'eef'})
    assert result.returncode==0,result.stderr
    assert [event['status'] for event in events]==['STARTED','COMPLETED']*2
