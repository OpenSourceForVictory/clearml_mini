import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from runner.mock_eval import evaluate


def test_reproducible():
    a = evaluate('job', 'model', 'mock', 5)
    assert a == evaluate('job', 'model', 'mock', 5)
    assert a['episode_count'] == 5
    assert 0 <= a['success_rate'] <= 1
