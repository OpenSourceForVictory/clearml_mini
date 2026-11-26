"""Real business adapters, isolated systemd/Docker, original-ID recovery."""
import os
import pytest
from backend.tests.test_recovery_execution_real import exercise


@pytest.mark.skipif(os.getenv('RECOVERY_RUNTIME_TEST') != '1', reason='real recovery protocol matrix opt-in')
@pytest.mark.parametrize('mode,launch', [('host', 'whole_task'), ('host', 'per_episode'), ('docker', 'whole_task')])
@pytest.mark.parametrize('integration', ['command', 'direct-protocol', 'sdk', 'sdk-command'])
def test_real_business_adapters_finish_offline_and_report_all_episodes(tmp_path, mode, launch, integration):
    exercise(tmp_path, mode, 'offline_complete', (integration, launch))


@pytest.mark.skipif(os.getenv('RECOVERY_RUNTIME_TEST') != '1', reason='real task-result SDK recovery opt-in')
@pytest.mark.parametrize('mode', ['host', 'docker'])
def test_real_task_report_sdk_preserves_result_after_offline_exit(tmp_path, mode):
    exercise(tmp_path, mode, 'offline_complete', ('sdk-task-result', 'whole_task'))
