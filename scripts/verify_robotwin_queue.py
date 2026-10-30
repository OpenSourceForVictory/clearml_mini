"""Verify saved real-run evidence; never treats terminal failures as success."""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path


def summarize(state):
    jobs = state.get('final_jobs', [])
    history = state.get('history', [])
    nodes = {job['id']: job['node'] for snap in history for job in snap['jobs'] if job.get('node')}
    active = {'RESERVED', 'STARTING', 'RUNNING', 'CANCEL_REQUESTED'}
    overlaps = []
    for index, first in enumerate(jobs):
        for second in jobs[index + 1:]:
            first_node, second_node = nodes.get(first['id']), nodes.get(second['id'])
            if not first_node or not second_node or first_node == second_node:
                continue
            if not all(job.get(key) for job in (first, second) for key in ('started_at', 'finished_at')):
                continue
            start = max(datetime.fromisoformat(job['started_at']) for job in (first, second))
            end = min(datetime.fromisoformat(job['finished_at']) for job in (first, second))
            if end > start:
                overlaps.append({'jobs': [first['id'], second['id']], 'nodes': [first_node, second_node],
                                 'seconds': (end - start).total_seconds()})
    waited = {job['id'] for snap in history for job in snap['jobs'] if job['status'] == 'PENDING'}
    started = {job['id'] for job in jobs if job.get('started_at')}
    episodes = state.get('episodes', {})
    completed_checks = 0
    for job in jobs:
        evidence = episodes.get(job['id'], {})
        items = evidence.get('items', [])
        if (evidence.get('progress', {}).get('completed') == 1 and len(items) == 1
                and items[0].get('result', {}).get('success') is True
                and items[0].get('result', {}).get('status') == 'COMPLETED'
                and items[0].get('result', {}).get('metrics', {}).get('model_evaluation') == 0
                and items[0].get('result', {}).get('metrics', {}).get('environment_check') == 1):
            completed_checks += 1
    peak = max((sum(j['status'] in active for j in s['jobs']) for s in history), default=0)
    pending = max((sum(j['status'] == 'PENDING' for j in s['jobs']) for s in history), default=0)
    counts = Counter(nodes.get(j['id'], 'unknown') for j in jobs)
    events = []
    for job in jobs:
        if job.get('started_at') and job.get('finished_at'):
            events.extend([(datetime.fromisoformat(job['started_at']), 1),
                           (datetime.fromisoformat(job['finished_at']), -1)])
    running = actual_peak = 0
    for _, delta in sorted(events):
        running += delta
        actual_peak = max(actual_peak, running)
    checks = {
        'nine_successful_jobs': len(jobs) == 9 and len({j['id'] for j in jobs}) == 9
                               and all(j['status'] == 'SUCCEEDED' for j in jobs),
        'two_hosts_overlap': len(counts) == 2 and 'unknown' not in counts and bool(overlaps),
        'capacity_and_queue': peak == 2 and actual_peak == 2 and pending >= 7 and len(waited & started) >= 7,
        'nine_reported_scene_checks': completed_checks == 9,
    }
    return dict(suite_id=state['suite_id'], batches=state['batches'],
                statuses=dict(Counter(j['status'] for j in jobs)), node_counts=dict(counts),
                peak_active=peak, peak_running_from_timestamps=actual_peak, max_pending=pending, queued_then_started=len(waited & started),
                cross_node_overlap=overlaps, checks=checks, passed=all(checks.values()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('state', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = summarize(json.loads(args.state.read_text()))
    text = json.dumps(result, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(text + '\n')
    print(text)
    if not result['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
