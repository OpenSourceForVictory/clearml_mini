"""HTTP client protocol check, not a RoboTwin model evaluation."""
import json
import math
import os
from pathlib import Path
import random
import time
import urllib.request


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def evaluate(config):
    rng = random.Random(config['seed'])
    results = []
    started = time.monotonic()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    for episode in range(config['episodes']):
        position, target = 0, rng.choice([-3, -2, -1, 1, 2, 3])
        for step in range(10):
            body = {'task_name': config['task_name'], 'episode': episode, 'step': step,
                    'observation': {'position': position, 'target': target}, 'parameters': config['parameters']}
            req = urllib.request.Request(config['endpoint'], data=json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json'})
            try:
                with opener.open(req, timeout=config['request_timeout_seconds']) as response:
                    data = response.read(65537)
                if len(data) > 65536:
                    raise ValueError('response too large')
                action = json.loads(data)['action']
                if isinstance(action, bool) or not isinstance(action, (int, float)) or not math.isfinite(action) or action not in (-1, 0, 1):
                    raise ValueError('invalid action')
            except Exception as exc:
                raise RuntimeError('inference request failed, timed out or returned an invalid action') from exc
            position += action
            if position == target:
                break
        success = position == target
        results.append({'episode': episode, 'success': success, 'steps': step+1})
        print(json.dumps({'episode': episode+1, 'success': success, 'stage': 'protocol-check'}), flush=True)
    metrics = {'success_rate': sum(r['success'] for r in results)/len(results),
               'episode_count': len(results), 'runtime': time.monotonic()-started}
    return metrics, results


def main():
    config = json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
    metrics, results = evaluate(config)
    output = Path(os.environ['MINI_EVAL_OUTPUT_DIR'])
    output.mkdir(parents=True, exist_ok=True)
    (output/'report.json').write_text(json.dumps({'kind': 'cs-protocol-check', 'task_name': config['task_name'],
        'seed': config['seed'], 'metrics': metrics, 'episodes': results}, indent=2))
    print(json.dumps({'final': metrics}), flush=True)


if __name__ == '__main__':
    main()
