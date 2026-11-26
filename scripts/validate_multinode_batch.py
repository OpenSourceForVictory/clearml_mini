"""Submit an expendable multi-node command batch through the public API."""
import argparse
from collections import Counter
from datetime import datetime
import getpass
import json
from pathlib import Path
import time
import uuid

import httpx


def max_overlap(intervals):
    """Return peak simultaneous executions and nodes using recorded timestamps."""
    events = []
    for node, start, end in intervals:
        begin = datetime.fromisoformat(start.replace('Z', '+00:00'))
        finish = datetime.fromisoformat(end.replace('Z', '+00:00'))
        if finish < begin:
            raise ValueError('execution finished before it started')
        if finish == begin:
            continue
        events.extend(((begin, 1, node), (finish, -1, node)))
    active = Counter()
    peak_tasks = peak_nodes = 0
    for _, delta, node in sorted(events, key=lambda item: (item[0], item[1])):
        active[node] += delta
        if active[node] <= 0:
            active.pop(node, None)
        peak_tasks = max(peak_tasks, sum(active.values()))
        peak_nodes = max(peak_nodes, len(active))
    return peak_tasks, peak_nodes


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:18000/api/v1/')
    parser.add_argument('--pool-id', required=True)
    parser.add_argument('--image', default='mini-eval-runner:dev')
    parser.add_argument('--username', default='admin')
    parser.add_argument('--tasks', type=int, default=50)
    parser.add_argument('--max-concurrent', type=int, default=5)
    parser.add_argument('--min-nodes', type=int, default=3)
    parser.add_argument('--sleep-seconds', type=int, default=8)
    parser.add_argument('--deadline-seconds', type=int, default=900)
    parser.add_argument('--evidence', type=Path)
    args = parser.parse_args()
    if not 2 <= args.tasks <= 100 or not 2 <= args.max_concurrent <= 100:
        parser.error('tasks and max-concurrent must fit the batch API limits')
    if not 2 <= args.min_nodes <= args.max_concurrent or not 1 <= args.sleep_seconds <= 60:
        parser.error('min-nodes must fit concurrency, and sleep-seconds must be 1–60')
    if args.deadline_seconds < args.sleep_seconds + 30:
        parser.error('deadline-seconds must leave time for startup and cleanup')

    client = httpx.Client(base_url=args.base_url, trust_env=False, timeout=30)
    transient = Counter()

    def api(method, path, body=None):
        for attempt in range(6):
            reply = client.request(method, path, json=body)
            if reply.status_code not in (429, 502, 503, 504):
                if not reply.is_success:
                    raise RuntimeError(f'{method} {path}: HTTP {reply.status_code}: {reply.text[:400]}')
                return reply.json()
            transient[str(reply.status_code)] += 1
            time.sleep(min(2, 0.25 * 2**attempt))
        raise RuntimeError(f'{method} {path}: temporary API failures persisted')

    batch_id = template_id = None
    evidence = {'model_evaluation': False, 'task_count': args.tasks,
                'max_concurrent': args.max_concurrent, 'pool_id': args.pool_id}
    try:
        password = getpass.getpass('Platform password: ')
        token = api('POST', 'auth/login', {'username': args.username, 'password': password})['access_token']
        del password
        client.headers['Authorization'] = 'Bearer ' + token
        pool = next((p for p in api('GET', 'admin/pools') if p['id'] == args.pool_id), None)
        if not pool or not pool['enabled'] or pool['ready_count'] < args.min_nodes:
            raise RuntimeError('selected pool does not have enough ready members')
        evidence['pool_name'] = pool['name']
        suffix = uuid.uuid4().hex[:8]
        command = ('import json,os,time; time.sleep(' + str(args.sleep_seconds)
                   + '); print("MULTINODE_SMOKE "+json.dumps({"pid":os.getpid(),'
                   + '"visible":os.getenv("CUDA_VISIBLE_DEVICES")}),flush=True)')
        template = api('POST', 'admin/client-templates', {
            'name': '多节点50任务验收 ' + suffix, 'version': '1',
            'description': '容器普通命令调度与并发验收；非模型评测。',
            'image': args.image, 'execution_mode': 'docker',
            'integration_mode': 'direct', 'task_type': 'command',
            'command': ['python3', '-c', command], 'runtime': {},
        })
        template_id = template['id']
        evidence['template_id'] = template_id
        body = {
            'title': '多节点并发验收 ' + suffix, 'pool_id': args.pool_id,
            'max_concurrent': args.max_concurrent,
            'client': {'template_id': template_id, 'service_mode': 'none',
                       'timeout_seconds': max(120, args.deadline_seconds)},
            'resources': {'accelerator_type': 'gpu', 'gpu_mode': 'exclusive',
                          'cpus': 1, 'ram_gb': 1, 'cpu_unlimited': True,
                          'memory_unlimited': True},
            'cases': [{'case_id': f'case-{n:03d}', 'task_name': f'multinode-{n:03d}',
                       'episodes': 1, 'parameters': {}} for n in range(args.tasks)],
        }
        preflight = api('POST', 'batches/preflight', body)
        if preflight.get('status') != 'ready':
            evidence['preflight'] = preflight
            raise RuntimeError('preflight did not pass; no batch submitted')
        batch = api('POST', 'batches', body)
        batch_id = evidence['batch_id'] = batch['id']
        print('submitted batch', batch_id, flush=True)
        peak_observed = 0
        deadline = time.monotonic() + args.deadline_seconds
        while time.monotonic() < deadline:
            view = api('GET', 'batches/' + batch_id)
            active = sum(c['status'] in ('RESERVED', 'ENQUEUED', 'STARTING', 'RUNNING') for c in view['cases'])
            peak_observed = max(peak_observed, active)
            if view['finished'] == args.tasks:
                break
            time.sleep(2)
        else:
            raise TimeoutError('batch did not finish before deadline')
        evidence['status'] = view['status']
        evidence['succeeded'] = view['succeeded']
        evidence['finished'] = view['finished']
        evidence['observed_active_peak'] = peak_observed
        evidence['nodes'] = dict(Counter(c['placement']['node_name'] for c in view['cases']
                                         if c.get('placement') and c['placement'].get('node_name')))
        evidence['failures'] = [{'case_id': c['case_id'], 'status': c['status'], 'error': c['error']}
                                for c in view['cases'] if c['status'] != 'SUCCEEDED']
        evidence['exit_codes'] = dict(Counter(str(c['outcomes'].get('wrapper_exit_code'))
                                              for c in view['cases']))
        markers = {}
        for node in evidence['nodes']:
            case = next(c for c in view['cases'] if (c.get('placement') or {}).get('node_name') == node)
            logs = api('GET', 'jobs/' + case['job_id'] + '/logs?run_id=' + case['run_id'] + '&after=-1&limit=100')
            markers[node] = any('MULTINODE_SMOKE' in chunk['content'] for chunk in logs['chunks'])
        evidence['sample_command_marker_by_node'] = markers
        intervals = []
        cleanup = 0
        for case in view['cases']:
            job = api('GET', 'jobs/' + case['job_id'])
            run = job['attempts'][-1]
            cleanup += bool(run['cleanup_confirmed'])
            timing = run['timing']
            start = timing.get('running_started_at')
            end = timing['execution'].get('finished_at')
            node = (case.get('placement') or {}).get('node_name')
            if node and start and end:
                intervals.append((node, start, end))
        evidence['cleanup_confirmed'] = cleanup
        evidence['recorded_intervals'] = len(intervals)
        evidence['recorded_task_peak'], evidence['recorded_node_peak'] = max_overlap(intervals)
        evidence['transient_http'] = dict(transient)
        assert (view['succeeded'] == args.tasks and cleanup == args.tasks
                and len(evidence['nodes']) >= args.min_nodes
                and len(intervals) == args.tasks
                and evidence['recorded_node_peak'] >= args.min_nodes
                and evidence['recorded_task_peak'] <= args.max_concurrent
                and evidence['exit_codes'] == {'0': args.tasks}
                and all(markers.values())), evidence
        print(json.dumps(evidence, ensure_ascii=False, indent=2), flush=True)
    except Exception as exc:
        evidence['error'] = str(exc)[:1000]
        raise
    finally:
        if batch_id and evidence.get('succeeded') != args.tasks:
            try:
                view = api('GET', 'batches/' + batch_id)
                api('POST', 'batches/' + batch_id + '/control',
                    {'action': 'cancel', 'expected_revision': view['control_revision']})
            except Exception as exc:
                evidence['cancel_error'] = str(exc)[:300]
        if template_id:
            try:
                api('PATCH', 'admin/client-templates/' + template_id, {'enabled': False})
            except Exception as exc:
                evidence['template_disable_error'] = str(exc)[:300]
        evidence['transient_http'] = dict(transient)
        if args.evidence:
            args.evidence.parent.mkdir(parents=True, exist_ok=True)
            args.evidence.write_text(json.dumps(evidence, ensure_ascii=False, indent=2) + '\n')
        client.close()


if __name__ == '__main__':
    main()
