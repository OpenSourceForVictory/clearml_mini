import csv
import io
import json
import pytest
from sqlalchemy import event
from backend.tests.test_execution import env
from backend.tests.test_monitoring import batch
from backend.tests.test_preflight import setup
from backend.app.core import Job, Run, EpisodeRecord
from backend.app.scheduling import transition
from backend.app.reports import compare, csv_text


def get(env, b, fmt='json', owner='alice'):
    _, c, auth = env
    return c.get(f"/api/v1/batches/{b['id']}/report?format={fmt}", headers=auth[owner])


def populate(env, b):
    main, _, _ = env
    with main.SessionLocal() as s:
        for n, item in enumerate(b['cases']):
            j = s.get(Job, item['job_id'])
            j.episodes = n+1
            j.client_spec = {**j.client_spec, 'parameters': {'secret': 'hidden-value'},
                             'environment': {'PASSWORD': 'hidden-value'},
                             'template': {**j.client_spec['template'], 'episode_reporting': True}}
            r = s.get(Run, item['run_id']); transition(s, r, 'SUCCEEDED')
            for i in range(n+1):
                s.add(EpisodeRecord(run_id=r.id, episode_id=i, seed=0, started={},
                                    result={'status': 'COMPLETED', 'success': n == 0}))
        s.commit()


def test_weighted_privacy_csv_and_permissions(env):
    b, _ = batch(env); populate(env, b)
    response = get(env, b); assert response.status_code == 200, response.text
    r = response.json()
    assert r['summary']['success_rate'] == 1/3
    assert r['summary']['goal_failures'] == 2 and not r['summary']['partial']
    assert 'hidden-value' not in response.text and 'PASSWORD' not in response.text
    assert get(env, b, owner='bob').status_code == 404
    assert get(env, b, owner='admin').status_code == 200
    assert get(env, b, fmt='xml').status_code == 400
    raw = get(env, b, 'csv')
    assert raw.content.startswith(b'\xef\xbb\xbf')
    rows = list(csv.DictReader(io.StringIO(raw.content.decode('utf-8-sig'))))
    assert len(rows) == 2 and rows[0]['attempt'] == '1'
    assert 'hidden-value' not in raw.text


def test_latest_retry_partial_error_and_unreported(env):
    main, c, auth = env; b, _ = batch(env); populate(env, b)
    with main.SessionLocal() as s:
        r = s.get(Run, b['cases'][1]['run_id']); transition(s, r, 'FAILED'); r.cleanup_confirmed = True
        s.add(EpisodeRecord(run_id=r.id, episode_id=99, seed=0, started={}, result={'status': 'ERROR'})); s.commit()
    r = get(env, b).json(); assert r['summary']['episode_errors'] == 1 and r['summary']['partial']
    assert c.post('/api/v1/jobs/'+b['cases'][1]['job_id']+'/retry', headers=auth['alice']).status_code == 202
    r = get(env, b).json()
    assert r['summary']['completed_episodes'] == 1 and r['summary']['success_rate'] == 1
    assert r['cases'][1]['attempt'] == 2 and r['cases'][1]['success_rate'] is None
    assert r['cases'][1]['run_id'] != b['cases'][1]['run_id']
    with main.SessionLocal() as s:
        j = s.get(Job, b['cases'][0]['job_id'])
        j.client_spec = {**j.client_spec, 'template': {**j.client_spec['template'], 'episode_reporting': False}}
        s.commit()
    assert get(env, b).json()['summary']['success_rate'] is None


def test_comparison_rules_and_permissions(env):
    _, c, auth = env; a, _ = batch(env); populate(env, a)
    # Copy only the public report to exercise each independent eligibility condition.
    left = get(env, a).json(); right = json.loads(json.dumps(left))
    right['cases'][1]['success_rate'] = .5
    right['summary']['success_rate'] = 2/3
    result = compare(left, right)
    assert result['comparable'] and result['delta_success_rate'] == 1/3
    assert result['cases'][1]['delta_success_rate'] == .5
    for field, value in [('task_name', 'different'), ('seed', -1), ('episodes', 5), ('parameters_digest', 'different')]:
        changed = json.loads(json.dumps(right)); changed['cases'][0][field] = value
        result = compare(left, changed)
        assert not result['comparable'] and result['cases'][0]['definition_differences'] == [field]
    changed = json.loads(json.dumps(right)); changed['cases'].pop()
    assert compare(left, changed)['cases'][1]['reasons'] == ['missing_right']
    right['cases'][0]['episode_reporting'] = False
    assert 'unreported' in compare(left, right)['cases'][0]['reasons']
    right['cases'][0]['partial'] = True
    assert 'incomplete' in compare(left, right)['cases'][0]['reasons']
    path = '/api/v1/batch-comparisons'
    assert c.get(path, params={'left_id': a['id'], 'right_id': a['id']}, headers=auth['alice']).status_code == 422
    assert c.get(path, params={'left_id': a['id'], 'right_id': 'missing'}, headers=auth['alice']).status_code == 404
    # A second actual batch avoids relying on compare() for access control.
    from backend.app.core import EvaluationBatch
    with env[0].SessionLocal() as s:
        foreign = EvaluationBatch(owner_id='bob', title='secret'); s.add(foreign); s.commit(); identity = foreign.id
    response = c.get(path, params={'left_id': a['id'], 'right_id': identity}, headers=auth['alice'])
    assert response.status_code == 404 and 'secret' not in response.text
    assert c.get(path, params={'left_id': a['id'], 'right_id': identity}, headers=auth['admin']).status_code == 200


@pytest.mark.parametrize('value', ['=1+1', '  +cmd', '-cmd', '@SUM(1)', '\ttext', '\ntext', '  \ttext', '\x7ftext'])
def test_csv_formula(value):
    assert csv_text(value) == "'"+value


def test_csv_roundtrip_and_numbers(env):
    from backend.app.reports import export_csv
    title = '中文,"标题"\n第二行'
    raw = export_csv({'schema_version': 1, 'generated_at': 'now', 'batch': {'id': 'id', 'title': title},
                      'cases': [{'case_id': '=cmd', 'seed': -12, 'success_rate': None}]})
    row = next(csv.DictReader(io.StringIO(raw.decode('utf-8-sig'))))
    assert row['batch_title'] == title and row['case_id'] == "'=cmd"
    assert row['seed'] == '-12' and row['success_rate'] == ''


def test_report_100_cases_query_budget(env):
    main, c, auth = env; body, _ = setup(env)
    body['cases'] = [{**body['cases'][0], 'case_id': str(i)} for i in range(100)]
    b = c.post('/api/v1/batches', headers=auth['alice'], json=body).json()
    queries = []; engine = main.SessionLocal.kw['bind']
    def trace(*args): queries.append(args[2])
    event.listen(engine, 'before_cursor_execute', trace)
    try: r = get(env, b).json()
    finally: event.remove(engine, 'before_cursor_execute', trace)
    assert len(queries) <= 7, queries
    assert r['summary']['unreported_tasks'] == 100
    assert all('FOR UPDATE' not in q.upper() for q in queries)


def test_cancelled_partial_config_differences_and_real_comparison(env):
    main,c,auth=env;b,_=batch(env);populate(env,b)
    from backend.app.core import EvaluationBatch
    with main.SessionLocal() as s:
        original=s.get(EvaluationBatch,b['id'])
        other=EvaluationBatch(owner_id=original.owner_id,title='other');s.add(other);s.flush();other_id=other.id
        for item in b['cases']:
            j=s.get(Job,item['job_id'])
            clone=Job(owner_id=j.owner_id,pool_id=j.pool_id,batch_id=other.id,case_id=j.case_id,
                      title=j.title,benchmark=j.benchmark,model_ref=j.model_ref,episodes=j.episodes,status='SUCCEEDED',
                      client_spec={**j.client_spec,'template':{**j.client_spec['template'],'image':'new:2'}})
            s.add(clone);s.flush();r=Run(job_id=clone.id,attempt=1,status='SUCCEEDED');s.add(r);s.flush()
            for i in range(j.episodes):s.add(EpisodeRecord(run_id=r.id,episode_id=i,seed=0,started={},result={'status':'COMPLETED','success':True}))
        s.commit()
    path=f"/api/v1/batch-comparisons?left_id={b['id']}&right_id={other_id}"
    value=c.get(path,headers=auth['alice']).json()
    assert value['comparable'] and value['delta_success_rate']==pytest.approx(2/3)
    assert value['cases'][0]['config_differences']==['image']
    assert value['left']['generated_at']==value['right']['generated_at']
    with main.SessionLocal() as s:
        transition(s,s.get(Run,b['cases'][0]['run_id']),'CANCELLED');s.commit()
    value=get(env,b).json()
    assert value['summary']['partial'] and value['summary']['completed_episodes']==3
    assert 'incomplete' in c.get(path,headers=auth['alice']).json()['cases'][0]['reasons']
