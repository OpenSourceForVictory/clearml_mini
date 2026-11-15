"""Opt-in real mount disappearance; does not disconnect or mount production NFS."""
import os
from pathlib import Path
import subprocess
import pytest


@pytest.mark.skipif(os.getenv('RUN_AGENT_MOUNT_TEST')!='1',reason='disposable privileged mount namespace required')
def test_workspace_mount_loss_does_not_break_local_agent_state():
    root=Path(__file__).resolve().parents[3]
    result=subprocess.run(['docker','run','--rm','--privileged','--network','none',
        '-e','PYTHONPATH=/repo','-v',str(root)+':/repo:ro','-w','/repo','--entrypoint','python',
        os.environ.get('STORAGE_PROBE_TEST_IMAGE','mini-eval-client-base:1'),
        'backend/tests/fixtures/agent_mount_loss.py'],capture_output=True,text=True,timeout=45)
    assert result.returncode==0,result.stdout+result.stderr
    assert '"missing_workspace": "rejected"' in result.stdout
    assert '"local_agent_state": "durable"' in result.stdout
