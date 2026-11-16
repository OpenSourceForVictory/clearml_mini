import argparse,json,os,time,urllib.request
from pathlib import Path
from common import load

parser=argparse.ArgumentParser();parser.add_argument('--variant',type=int,required=True)
args=parser.parse_args();config=load()
assert args.variant==config['business']['variant']
episode=int(os.environ['MINI_EVAL_EPISODE_ID'])
url=config['platform']['service']['endpoint']
payload={'case_id':config['task']['case_id'],'execution_id':config['platform']['execution_id'],'episode':episode,'parameters':config['business']}
request=urllib.request.Request(url,data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=5) as response:result=json.load(response)
assert result['received']==payload and result['model']=='test-model' and result['server_label']=='server-label'
time.sleep(config['business']['sleep_seconds'])
if config['business']['fail']:raise RuntimeError('intentional acceptance failure')
name=f'episode-{episode}.json'
value={'case':config['task']['case_id'],'endpoint':url,'business':config['business'],'server':result}
(Path(os.environ['MINI_EVAL_OUTPUT_DIR'])/name).write_text(json.dumps(value))
print(json.dumps({'verified_input':value}),flush=True)
Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({'success':True,'metrics':{'echo_verified':1,'asset_verified':1},'artifacts':[name]}))
