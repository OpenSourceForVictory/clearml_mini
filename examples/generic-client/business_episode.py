"""Deterministic protocol example, not a real simulation or model evaluation."""
import json
import os
from pathlib import Path

Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({
    'success': True,
    'metrics': {'example_seed': int(os.environ['MINI_EVAL_EPISODE_SEED'])},
    'artifacts': [],
}))
