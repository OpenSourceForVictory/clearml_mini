"""No episode reporting: exit 0 only means this command completed."""
import json
import os
from pathlib import Path

config = json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
output = Path(os.environ['MINI_EVAL_OUTPUT_DIR'])
output.mkdir(parents=True, exist_ok=True)
(output / 'command-result.json').write_text(json.dumps({'task': config['task']['task_name']}))
print('Command completed; no evaluation success rate is claimed.', flush=True)
