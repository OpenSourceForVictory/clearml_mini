"""One invocation = one episode; the SDK owns the outer loop."""
import json
import os
from pathlib import Path

config = json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({
    'success': not config['business'].get('force_failure', False),
    'metrics': {'seed': int(os.environ['MINI_EVAL_EPISODE_SEED'])},
    'artifacts': [],
}))
