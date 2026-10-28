import argparse
import hashlib
import json
import os
import time


def evaluate(job_id, model_ref, benchmark, episodes):
    records = []
    for index in range(episodes):
        seed = f'{job_id}:{model_ref}:{benchmark}:{index}'.encode()
        success = int(hashlib.sha256(seed).digest()[0] < 180)
        records.append({'episode': index + 1, 'success': success})
    return {'success_rate': sum(x['success'] for x in records) / episodes,
            'episode_count': episodes, 'episodes': records}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--job-id', required=True)
    parser.add_argument('--model-ref', required=True)
    parser.add_argument('--benchmark', default='mock')
    parser.add_argument('--episodes', type=int, required=True)
    args = parser.parse_args()
    if args.episodes < 1:
        parser.error('episodes must be positive')
    started = time.monotonic()
    result = evaluate(args.job_id, args.model_ref, args.benchmark, args.episodes)
    for item in result['episodes']:
        print(json.dumps(item), flush=True)
        time.sleep(float(os.getenv('MOCK_EPISODE_SLEEP', '0')))
    if os.getenv('MOCK_FAIL') == '1':
        raise RuntimeError('injected failure')
    result.pop('episodes')
    result['runtime'] = time.monotonic() - started
    print(json.dumps({'final': result}), flush=True)


if __name__ == '__main__':
    main()
