from copy import deepcopy
from scripts.verify_robotwin_queue import summarize


def evidence():
    jobs = [dict(id=str(i), status='SUCCEEDED', started_at=f'2026-10-30T00:0{i // 2}:10+00:00',
                 finished_at=f'2026-10-30T00:0{i // 2}:20+00:00') for i in range(9)]
    pending = [dict(id=j['id'], status='PENDING', node=None) for j in jobs]
    active = deepcopy(pending)
    active[0].update(status='RUNNING', node='a')
    active[1].update(status='RUNNING', node='b')
    final = [dict(id=j['id'], status=j['status'], node='a' if i % 2 else 'b') for i, j in enumerate(jobs)]
    episodes = {j['id']: dict(progress=dict(completed=1), items=[dict(result=dict(
        status='COMPLETED', success=True, metrics=dict(environment_check=1, model_evaluation=0)))]) for j in jobs}
    return dict(suite_id='suite', batches=['1', '2', '3'], final_jobs=jobs,
                history=[dict(jobs=pending), dict(jobs=active), dict(jobs=final)], episodes=episodes)


def test_requires_successful_real_scene_results():
    state = evidence()
    assert summarize(state)['passed']
    state['final_jobs'][0]['status'] = 'FAILED'
    assert not summarize(state)['passed']
    state['final_jobs'][0]['status'] = 'SUCCEEDED'
    state['episodes']['0']['items'][0]['result']['success'] = False
    assert not summarize(state)['passed']


def test_requires_known_hosts_overlapping_execution_and_queue():
    state = evidence()
    for snap in state['history']:
        for job in snap['jobs']:
            if job['node']:
                job['node'] = 'one-host'
    assert not summarize(state)['checks']['two_hosts_overlap']
    state = evidence()
    state['history'] = state['history'][2:]
    assert not summarize(state)['checks']['capacity_and_queue']
    state = evidence()
    for i, job in enumerate(state['final_jobs']):
        job['started_at'] = f'2026-10-30T00:{i:02d}:00+00:00'
        job['finished_at'] = f'2026-10-30T00:{i:02d}:01+00:00'
    assert not summarize(state)['checks']['two_hosts_overlap']
