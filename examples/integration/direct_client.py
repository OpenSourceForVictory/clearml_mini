"""Language-neutral JSON-lines protocol demonstrated with Python stdlib."""
import json
import os
from pathlib import Path
import time

config = json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
plan = config['task']
started = time.monotonic()
successes = 0
for episode_id in range(1, plan['episodes'] + 1):
    seed = (plan['seed'] + episode_id - 1) % 2147483648
    base = {'episode_id': episode_id, 'seed': seed}
    print('MINI_EVAL_EVENT ' + json.dumps({**base, 'status': 'STARTED'}), flush=True)
    begin = time.monotonic()
    # Replace this predicate with real evaluation and handle errors as ERROR events.
    success = not config['business'].get('force_failure', False)
    successes += int(success)
    print('MINI_EVAL_EVENT ' + json.dumps({**base, 'status': 'COMPLETED',
        'success': success, 'metrics': {}, 'artifacts': [],
        'runtime': time.monotonic() - begin}), flush=True)
print(json.dumps({'final': {'success_rate': successes / plan['episodes'],
    'episode_count': plan['episodes'], 'runtime': time.monotonic() - started}}), flush=True)
