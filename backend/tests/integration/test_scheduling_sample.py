"""Execute the shipped suite and templates, not hand-written substitute payloads."""
import os
import pytest
from backend.tests.test_execution import env
from backend.tests.integration.test_orchestration_execution import test_real_two_logical_nodes_three_cases_multiple_batches as execute_sample

pytestmark=pytest.mark.skipif(os.getenv('ORCHESTRATION_EXECUTION_TEST')!='1',reason='real host/docker sample opt-in')


@pytest.mark.parametrize('mode',['host','docker'])
@pytest.mark.parametrize('access',['shared','exclusive'])
def test_shipped_sample(env,monkeypatch,tmp_path,mode,access):
    execute_sample(env,monkeypatch,tmp_path,mode,access,sample=True)


@pytest.mark.parametrize('mode',['host','docker'])
@pytest.mark.parametrize('fault',['failure','cancel'])
def test_sample_failure_and_cancel_release_resources(env,monkeypatch,tmp_path,mode,fault):
    execute_sample(env,monkeypatch,tmp_path,mode,'shared',sample=True,fault=fault)
