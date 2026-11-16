"""One real WebSocket round trip per SDK-command episode."""
import asyncio,json,os,time
from pathlib import Path
import aiohttp
c=json.loads(Path(os.environ['MINI_EVAL_INPUT']).read_text())
async def main():
    endpoint=c['platform']['service']['endpoint']
    assert endpoint.startswith('ws://')
    payload={'case_id':c['task']['case_id'],'episode_id':int(os.environ['MINI_EVAL_EPISODE_ID']),'variant':c['business']['variant']}
    async with aiohttp.ClientSession(trust_env=False) as session:
        async with session.ws_connect(endpoint+'/infer') as ws:
            opened_at=time.time()
            await ws.send_json(payload)
            result=await asyncio.wait_for(ws.receive_json(),5)
            assert result['echo']==payload and result['model']=='fleet-test-model'
            await asyncio.sleep(c['business'].get('sleep_seconds',1))
            closed_at=time.time()
    print(json.dumps({'ws_verified':True,'endpoint':endpoint,'request':payload,'server_execution':result['server_execution'],'pid':os.getpid(),'ws_opened_at':opened_at,'ws_closed_at':closed_at}),flush=True)
    Path(os.environ['MINI_EVAL_RESULT']).write_text(json.dumps({'success':True,'metrics':{'ws_echo_verified':1}}))
asyncio.run(main())
