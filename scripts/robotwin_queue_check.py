"""Submit three real scene-check batches and retain scheduling/episode evidence. No model evaluation."""
import collections,datetime,json,os,time,urllib.request,uuid
from pathlib import Path
ROOT=os.environ.get('EVAL_API_URL','http://192.168.137.223:18000').rstrip('/')
BASE=ROOT+'/api/v1/'
STATE=Path(os.environ.get('QUEUE_STATE_FILE','.runtime/robotwin-queue-exercise.json'))
STATE.parent.mkdir(parents=True,exist_ok=True)
username=os.environ.get('EVAL_ADMIN_USERNAME','admin')
password=os.environ['EVAL_ADMIN_PASSWORD']
token='';token_at=0

def api(path,body=None,method=None,headers=None):
    global token,token_at
    if path!='auth/login' and (not token or time.monotonic()-token_at>1200):
        token=api('auth/login',{'username':username,'password':password})['access_token'];token_at=time.monotonic()
    data=json.dumps(body).encode() if body is not None else None
    request=urllib.request.Request(BASE+path,data,{'Content-Type':'application/json',**({'Authorization':'Bearer '+token} if token else {}),**(headers or {})},method=method)
    with urllib.request.urlopen(request,timeout=20) as response:return json.load(response)

if not STATE.exists():
    name='RoboTwin 三任务场景自检（无模型）'
    templates=api('client-templates')
    template=next((t for t in templates if t['name']==name and t['version']=='queue-v1'),None)
    if template is None:
        template=api('admin/client-templates',dict(name=name,version='queue-v1',image='mini-eval-robotwin-queue-check:1',command=['python','-m','runner.sdk','examples.cosmos_robotwin.queue_client:RobotwinEnvironmentClient'],protocols=['http'],episode_reporting=True,endpoint_exclusive=False))
    cases=json.loads(Path('examples/cosmos_robotwin/suite-queue-check.json').read_text())
    suites=api('suites');suite=next((s for s in suites if s['name']==name and s['version']=='1'),None)
    if suite is None:suite=api('suites',dict(name=name,version='1',template_id=template['id'],cases=cases))
    state=dict(template_id=template['id'],suite_id=suite['id'],pool_id=next(p['id'] for p in api('pools') if p['name']==os.environ.get('QUEUE_POOL_NAME','gpu-pool')),run_key=str(uuid.uuid4()),batches=[],history=[])
    STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2))
else:state=json.loads(STATE.read_text())
print('Registered suite:',state['suite_id'],flush=True)
if os.environ.get('QUEUE_REGISTER_ONLY')=='1':raise SystemExit()
for index in range(len(state['batches']),3):
    batch=api('batches',dict(title=f'RoboTwin 双机排队验证 {index+1}/3（无模型）',suite_id=state['suite_id'],pool_id=state['pool_id'],max_concurrent=2,gpu_mode='exclusive',requested_vram_gb=8,client=dict(template_id=state['template_id'],endpoint=ROOT,timeout_seconds=900,request_timeout_seconds=10)),headers={'Idempotency-Key':state['run_key']+'-'+str(index)})
    state['batches'].append(batch['id']);STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2));print('Submitted:',batch['id'],flush=True)
last=None
while True:
    try:
        batches=[api('batches/'+bid) for bid in state['batches']]
        jobs=[api('jobs/'+case['job_id']) for b in batches for case in b['cases']]
        workers=api('workers');nodes={w['id']:w['node_name'] for w in workers}
        snapshot=dict(at=datetime.datetime.now(datetime.timezone.utc).isoformat(),jobs=[dict(id=j['id'],status=j['status'],node=nodes.get(j['attempts'][-1]['slot_id']),started_at=j['started_at'],finished_at=j['finished_at']) for j in jobs])
        state['history'].append(snapshot);state['final_batches']=batches;state['final_jobs']=jobs
        STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2))
        brief=([(j['status'],nodes.get(j['attempts'][-1]['slot_id'])) for j in jobs])
        if brief!=last:print(snapshot['at'],dict(collections.Counter(j['status'] for j in jobs)),brief,flush=True);last=brief
        if all(j['status'] in ['SUCCEEDED','FAILED','CANCELLED','TIMED_OUT'] for j in jobs):
            state['episodes']={j['id']:api('jobs/'+j['id']+'/attempts/'+j['attempts'][-1]['id']+'/episodes') for j in jobs}
            STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2));break
    except Exception as error:print('Monitoring retry:',type(error).__name__,str(error),flush=True)
    time.sleep(5)
counts = dict(collections.Counter(job['status'] for job in state['final_jobs']))
print('All jobs terminal:', counts, flush=True)
if not all(job['status'] == 'SUCCEEDED' for job in state['final_jobs']):
    raise SystemExit('Validation failed: one or more jobs did not succeed; evidence retained.')
