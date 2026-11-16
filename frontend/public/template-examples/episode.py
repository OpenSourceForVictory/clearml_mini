"""One real HTTP request per episode; checks wiring, not model quality."""
import json
import os
from pathlib import Path
from urllib.request import Request, build_opener, ProxyHandler

def evaluate(config, episode_id):
    endpoint = config['platform']['service']['endpoint']
    payload = {'task': config['task']['task_name'], 'scene': config['business']['scene'], 'episode': episode_id}
    request = Request(endpoint, data=json.dumps(payload).encode(), headers={'Content-Type': 'application/json'})
    with build_opener(ProxyHandler({})).open(request, timeout=10) as response:
        result = json.load(response)
    return result['received'] == payload and result['model'] == 'echo-demo'

if __name__ == '__main__':
    config = json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
    success = evaluate(config, int(os.environ['MINI_EVAL_EPISODE_ID']))
    Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({'success': success, 'metrics': {'echo_verified': int(success)}, 'artifacts': []}))
