import {test,expect,type Page} from '@playwright/test';
async function setup(page:Page){
  let offline=false,run='r1',lose=false,delay=false;const requests:any[]=[];
  const cases=['FAILED','TIMED_OUT','SUCCEEDED','PENDING'].map((status,i)=>({job_id:'j'+i,case_id:'case'+i,task_name:'task'+i,status,run_id:i===0?run:'r'+i,attempt:1,episodes:2,seed:0,episode_reporting:true,progress:{completed:1,successes:0},node_name:null,gpu_index:null,reason_codes:status==='PENDING'?['gpu_busy']:[],waiting:status==='PENDING'?{reasons:[{code:'gpu_busy',message:'GPU 已占用'}],nodes:[]}:null,config:{pool_name:'mixed',client_name:'client',client_version:'1',endpoint:'http://server',gpu_mode:'exclusive',requested_vram_gb:0}}));
  await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
    if(path==='auth/me')data={id:'user',username:'user',role:'user'};
    if(path==='suites/manage')data={items:[],total:0};
    if(path==='pools')data=[{id:'p',name:'pool'}];
    if(path==='batches')data=[{id:'a',title:'批次 A',status:'PENDING',succeeded:1,total:4,finished:3,total_episodes:8,cases:[]}];
    if(path.endsWith('/monitor')){
      if(path.includes('/private/')){await route.fulfill({status:404,json:{detail:'batch not found'}});return;}
      if(offline){await route.abort();return;}
      if(delay&&path.includes('/a/'))await new Promise(r=>setTimeout(r,900));
      data={id:'a',title:path.includes('/b/')?'批次 B':'批次 A',max_concurrent:2,total:4,finished:3,total_episodes:8,cases:cases.map((c,i)=>({...c,run_id:i===0?run:c.run_id})),summary:{states:{FAILED:1,TIMED_OUT:1,SUCCEEDED:1,PENDING:1},completed_episodes:3,successes:0,goal_failures:3,episode_errors:0,execution_failures:2,unreported_tasks:0,success_rate:0}};
    }
    if(path.endsWith('/retry-failed')){requests.push(route.request().postDataJSON());if(lose){lose=false;await route.abort();return;}data={retried:0,skipped:2,items:[{job_id:'j0',outcome:'skipped',message:'尝试已变化，请刷新后重新选择',attempt:2},{job_id:'j1',outcome:'skipped',message:'旧执行尚未确认清理',attempt:1}]};}
    if(path==='jobs/j0')data={attempts:[{id:'old',attempt:1,status:'FAILED'},{id:'new',attempt:2,status:'FAILED'}]};
    if(path.endsWith('/logs'))data={chunks:[{content:new URL(route.request().url()).searchParams.get('run_id')+' log'}],next_after:1};
    if(path.endsWith('/episodes'))data={planned:2,progress:{completed:1,successes:0,errors:0},items:[]};
    if(path.endsWith('/artifacts'))data=[{id:'file',name:path.includes('/old/')?'old.json':'new.json',size_bytes:2}];
    await route.fulfill({json:data});
  });
  await page.goto('/#/cs/batches/a');
  await expect(page.getByRole('heading',{name:'批次 A',exact:true})).toBeVisible();
  return {requests,setRun:()=>{run='r-new';},lose:()=>{lose=true;},offline:(v:boolean)=>{offline=v;},delay:()=>{delay=true;}};
}
test('direct navigation, refresh, draft preservation, filtering and offline recovery',async({page})=>{
  const ctl=await setup(page);await page.reload();await expect(page.getByText('目标成功率 0.0%',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'返回批次列表'}).click();await page.getByLabel('批次名称',{exact:true}).fill('保留草稿');
  await page.getByRole('button',{name:'查看批次',exact:true}).click();
  await page.getByLabel('任务状态',{exact:true}).selectOption('PENDING');await expect(page.locator('td p').filter({hasText:'GPU 已占用'})).toBeVisible();
  ctl.offline(true);await expect(page.getByRole('alert')).toContainText('监控更新失败');ctl.offline(false);await expect(page.getByRole('alert')).toHaveCount(0);
  await expect(page.getByLabel('任务状态',{exact:true})).toHaveValue('PENDING');
  await page.getByRole('button',{name:'返回批次列表'}).click();await expect(page.getByLabel('批次名称',{exact:true})).toHaveValue('保留草稿');
  await page.getByRole('button',{name:'查看批次',exact:true}).click();await expect(page.getByLabel('任务状态',{exact:true})).toHaveValue('PENDING');
});
test('selected expected attempts survive polling and lost response; goal failures excluded',async({page})=>{
  const ctl=await setup(page);await page.getByRole('button',{name:'选择可补跑任务'}).click();await expect(page.getByLabel('选择补跑 case2')).not.toBeChecked();await expect(page.getByLabel('选择补跑 case2')).toBeDisabled();
  ctl.setRun();await page.waitForResponse(r=>r.url().endsWith('/monitor'));ctl.lose();await page.getByRole('button',{name:'补跑选中 2 项'}).click();await expect(page.getByRole('alert')).toContainText('保留原尝试选择');
  await page.getByRole('button',{name:'补跑选中 2 项'}).click();await expect(page.getByRole('status')).toContainText('旧执行尚未确认清理');
  expect(ctl.requests).toHaveLength(2);expect(ctl.requests[0]).toEqual(ctl.requests[1]);expect(ctl.requests[1].items[0].expected_run_id).toBe('r1');
});
test('history stays selected; delayed route responses cannot overwrite next batch; private hidden',async({page})=>{
  const ctl=await setup(page);await page.getByRole('button',{name:'排查任务',exact:true}).first().click();await page.getByLabel('执行尝试',{exact:true}).selectOption('old');
  await expect(page.getByRole('button',{name:'下载 old.json'})).toBeVisible();await expect(page.locator('pre')).toContainText('old log');
  await page.waitForResponse(r=>r.url().endsWith('/jobs/j0'));await expect(page.getByLabel('执行尝试',{exact:true})).toHaveValue('old');
  ctl.delay();await page.waitForRequest(r=>r.url().endsWith('/a/monitor'));await page.evaluate(()=>{location.hash='/cs/batches/b';});await expect(page.getByRole('heading',{name:'批次 B',exact:true})).toBeVisible();await page.waitForResponse(r=>r.url().endsWith('/b/monitor'));await expect(page.getByRole('heading',{name:'批次 A',exact:true})).toHaveCount(0);
  await page.evaluate(()=>{location.hash='/cs/batches/private';});await expect(page.getByRole('alert')).toContainText('batch not found');await expect(page.getByRole('heading',{name:'批次 B',exact:true})).toHaveCount(0);
});
