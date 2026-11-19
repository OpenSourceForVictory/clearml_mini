import {test,expect,type Page} from '@playwright/test';
const outcomes={execution:{normal:3,abnormal:0,running:0,unknown:0,not_started:0},protocol:{planned:3,success:0,failure:0,invalid:3,errors:0,unreported:0,not_applicable_tasks:0,valid:0,success_rate:null}};
async function setup(page:Page,detailMode?:'execution'|'episodes'){
  let offline=false,run='r1',lose=false,delay=false;const requests:any[]=[];
  const cases=['FAILED','TIMED_OUT','SUCCEEDED','PENDING'].map((status,i)=>({outcomes:{...outcomes,detail_mode:detailMode},job_id:'j'+i,case_id:'case'+i,task_name:'task'+i,status,run_id:i===0?run:'r'+i,attempt:1,episodes:2,seed:0,episode_reporting:true,progress:{completed:1,successes:0},placement:i===0?{id:'gpu-slot',node_name:'client-gpu',accelerator_type:'gpu',gpu_index:0,gpu_model:'Test GPU'}:null,node_name:null,gpu_index:null,reason_codes:status==='PENDING'?['gpu_busy']:[],waiting:status==='PENDING'?{reasons:[{code:'gpu_busy',message:'GPU 已占用'}],nodes:[]}:null,config:{pool_name:'mixed',client_name:'client',client_version:'1',endpoint:'http://server',gpu_mode:'exclusive',requested_vram_gb:0}}));
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
      data={outcomes,id:'a',title:path.includes('/b/')?'批次 B':'批次 A',max_concurrent:2,total:4,finished:3,total_episodes:8,cases:cases.map((c,i)=>({...c,run_id:i===0?run:c.run_id})),summary:{states:{FAILED:1,TIMED_OUT:1,SUCCEEDED:1,PENDING:1},completed_episodes:3,successes:0,goal_failures:3,episode_errors:0,execution_failures:2,unreported_tasks:0,success_rate:0}};
    }
    if(path.endsWith('/retry-failed')){requests.push(route.request().postDataJSON());if(lose){lose=false;await route.abort();return;}data={retried:0,skipped:2,items:[{job_id:'j0',outcome:'skipped',message:'尝试已变化，请刷新后重新选择',attempt:2},{job_id:'j1',outcome:'skipped',message:'旧执行尚未确认清理',attempt:1}]};}
    if(path==='jobs/j0')data={attempts:[{id:'old',attempt:1,status:'FAILED'},{id:'new',attempt:2,status:'FAILED'}]};
    if(path.endsWith('/logs'))data={chunks:[{content:new URL(route.request().url()).searchParams.get('run_id')+' log'}],next_after:1};
    if(path.endsWith('/execution-location'))data={execution_id:null,node_name:null,directories:[]};
    if(path.endsWith('/episodes'))data={planned:2,progress:{completed:1,successes:0,errors:0},items:[]};
    if(path.endsWith('/artifacts'))data=[{id:'file',name:path.includes('/old/')?'old.json':'new.json',size_bytes:2}];
    await route.fulfill({json:data});
  });
  await page.goto('/#/cs/batches/a');
  await expect(page.getByRole('heading',{name:'批次 A',exact:true})).toBeVisible();
  return {requests,setRun:()=>{run='r-new';},lose:()=>{lose=true;},offline:(v:boolean)=>{offline=v;},delay:()=>{delay=true;}};
}
test('direct navigation, refresh, draft preservation, filtering and offline recovery',async({page})=>{
  const ctl=await setup(page);await page.reload();await expect(page.getByRole('region',{name:'程序执行统计'})).toContainText('正常结束 3 次');
  await expect(page.getByRole('region',{name:'平台协议回合结果统计'})).toContainText('结果校验异常 3');
  await page.getByRole('button',{name:'返回批次列表'}).click();await page.getByRole('button',{name:'业务评测向导',exact:true}).click();await page.getByLabel('批次名称',{exact:true}).fill('保留草稿');await page.getByRole('button',{name:'评测运行与结果',exact:true}).click();
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

test('detail tabs preserve filtering and retry selection and contain narrow tables',async({page})=>{
  await setup(page);
  expect(await page.locator('.batch-detail-tabs').evaluate(el=>getComputedStyle(el).flexDirection)).toBe('row');
  await page.getByLabel('任务状态',{exact:true}).selectOption('FAILED');
  await page.getByLabel('选择补跑 case0').check();
  await page.getByRole('button',{name:'运行配置',exact:true}).click();
  await expect(page.getByRole('region',{name:'批次任务页'})).toBeHidden();
  await expect(page.getByRole('heading',{name:'本次运行配置',exact:true})).toBeVisible();
  await page.getByRole('button',{name:'服务端与关联',exact:true}).click();
  await expect(page.getByText('本批次没有平台托管的服务编排。',{exact:false})).toBeVisible();
  await page.getByRole('button',{name:'任务与结果',exact:true}).click();
  await expect(page.getByLabel('任务状态',{exact:true})).toHaveValue('FAILED');
  await expect(page.getByLabel('选择补跑 case0')).toBeChecked();
  await page.setViewportSize({width:390,height:844});
  expect(await page.locator('.batch-detail-table').evaluate(el=>el.scrollWidth>el.clientWidth)).toBe(true);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1)).toBe(true);
});

test('client placement is a separate column with GPU zero and unassigned states',async({page})=>{
 await setup(page);
 await expect(page.getByRole('columnheader',{name:'客户端分配位置'})).toBeVisible();
 await expect(page.getByRole('cell',{name:'client-gpu / GPU 0 型号：Test GPU'})).toBeVisible();
 await expect(page.getByText('未分配或位置记录不可用').first()).toBeVisible();
});

test('episode expansion separates protocol results, process exits and unreported rounds',async({page})=>{
 await setup(page);
 let source='protocol',code:number|null=0,reporting=true;
 await page.route('**/jobs/j0/attempts/*/episodes?*',async route=>{
  await route.fulfill({json:{planned:3,run_status:'FAILED',exit_code:code,result_source:source,episode_reporting:reporting,
   progress:{completed:1,successes:0,errors:1},items:[
    {episode_id:1,seed:7,started:{},result:{status:'COMPLETED',success:false,metrics:{},exit_code:0}},
    {episode_id:2,seed:8,started:{},result:{status:'ERROR',error:'missing protocol result',exit_code:0}},
    {episode_id:3,seed:9,started:null,result:null}]}})
 });
 await page.getByRole('button',{name:'查看回合',exact:true}).first().click();
 const episodes=page.getByRole('region',{name:'case0 回合状态'});
 await expect(episodes).toContainText('平台协议业务结果');
 await expect(episodes).toContainText('执行进程退出码：0');
 await expect(episodes).toContainText('业务失败');
 await expect(episodes).toContainText('回合异常（未分类）');
 await expect(episodes).toContainText('缺少协议回合结果');
 await expect(episodes.getByRole('row').nth(3)).toContainText('未上报');
 await expect(episodes).not.toContainText('业务成功 1');
 source='exit_code';code=7;
 await expect(episodes).toContainText('普通命令退出码');
 await expect(episodes).toContainText('执行进程退出码：7');
 code=null;await expect(episodes).toContainText('执行进程退出码：未上报');
 reporting=false;await expect(episodes).toContainText('整次执行');await expect(episodes.getByRole('table')).toHaveCount(0);await expect(episodes.getByRole('button',{name:'下一页回合'})).toHaveCount(0);await expect(episodes).toContainText('配置回合数 3');
 await page.getByRole('button',{name:'收起回合',exact:true}).click();
 await expect(episodes).toHaveCount(0);
});


test('zero exits and invalid protocol are separate at batch task and episode levels',async({page})=>{
 await setup(page);
 const summary=page.getByRole('region',{name:'程序执行统计'});
 await expect(summary).toContainText('正常结束 3 次');await expect(summary).toContainText('异常退出 0 次');
 await expect(page.getByRole('region',{name:'平台协议回合结果统计'})).toContainText('有效结果 0/3 回合');
 await page.getByLabel('任务状态',{exact:true}).selectOption('FAILED');
 await expect(summary).toContainText('正常结束 3 次');
 await page.route('**/jobs/j0/attempts/*/episodes?*',route=>route.fulfill({json:{outcomes,planned:3,run_status:'FAILED',exit_code:2,result_source:'protocol',episode_reporting:true,progress:{completed:0,successes:0,errors:3},items:[1,2,3].map(i=>({episode_id:i,seed:6+i,execution_state:'normal',protocol_state:'invalid',started:{episode_id:i},result:{status:'ERROR',exit_code:0,error:'缺少 final 结果'}}))}}));
 await page.getByRole('button',{name:'查看回合',exact:true}).click();
 const table=page.getByRole('table',{name:'逐回合状态'});
 for(let i=1;i<=3;i++){
  await expect(table.getByRole('row').nth(i)).toContainText('正常结束');
  await expect(table.getByRole('row').nth(i)).toContainText('结果校验异常');
  await expect(table.getByRole('row').nth(i).getByRole('cell').nth(4)).toHaveText('0');
 }
});


test('whole command hides rounds even with reporting flag and preserves exit diagnostics',async({page})=>{
 await setup(page,'execution');
 await expect(page.getByRole('button',{name:'查看回合',exact:true})).toHaveCount(0);
 await expect(page.getByRole('button',{name:'查看执行',exact:true})).toHaveCount(4);
 let code:number|null=0,status='SUCCEEDED',mode='execution';
 await page.route('**/jobs/j0/attempts/*/episodes?*',async route=>route.fulfill({json:{planned:10,run_status:status,exit_code:code,result_source:'exit_code',episode_reporting:true,
  outcomes:{detail_mode:mode,process_scope:'single_process',execution:{normal:code===0?1:0,abnormal:code===7?1:0,running:0,unknown:code===null?1:0,not_started:0},protocol:{planned:0,not_applicable_tasks:1}},
  progress:{completed:0,errors:0},items:[]}}));
 await page.getByRole('button',{name:'排查任务',exact:true}).first().click();
 const panel=page.getByRole('region',{name:'整次执行状态'});
 await expect(panel).toContainText('配置回合数 10');await expect(panel).toContainText('正常结束');
 await expect(page.getByRole('table',{name:'逐回合状态'})).toHaveCount(0);
 code=7;status='FAILED';await expect(panel).toContainText('退出码：7');await expect(panel).toContainText('执行异常');
 code=null;status='TIMED_OUT';await expect(panel).toContainText('超时');await expect(panel).toContainText('不能判定正常结束');
 mode='episodes';await expect(panel).toHaveCount(0);await expect(page.getByRole('table',{name:'逐回合状态'})).toBeVisible();
});

test('result summaries use two separate cards and stack on narrow screens',async({page})=>{
 await setup(page);
 for(const width of [1440,1024,390]){
  await page.setViewportSize({width,height:1000});
  const execution=(await page.getByRole('region',{name:'程序执行统计'}).boundingBox())!;
  const protocol=(await page.getByRole('region',{name:'平台协议回合结果统计'}).boundingBox())!;
  if(width>700){expect(protocol.x).toBeGreaterThanOrEqual(execution.x+execution.width);expect(Math.abs(protocol.width-execution.width)).toBeLessThan(2);}
  else expect(protocol.y).toBeGreaterThanOrEqual(execution.y+execution.height);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 }
});

test('summary details are collapsed, expandable and preserved across polling',async({page})=>{
 await setup(page);const execution=page.getByRole('region',{name:'程序执行统计'}),protocol=page.getByRole('region',{name:'平台协议回合结果统计'});
 await expect(execution.getByText('正常退出率 100.0%（3/3）')).toBeVisible();await expect(protocol.getByText('评测成功率 暂无有效结果')).toBeVisible();
 await expect(execution.locator('details')).not.toHaveAttribute('open','');await expect(protocol.locator('details')).not.toHaveAttribute('open','');
 await execution.getByText('查看程序执行详情',{exact:true}).click();await protocol.getByText('查看回合结果详情',{exact:true}).click();
 await expect(execution.getByText(/正常结束 3 次/)).toBeVisible();await expect(protocol.getByText(/结果校验异常 3/)).toBeVisible();
 await page.waitForResponse(r=>r.url().includes('/monitor'));
 await expect(execution.locator('details')).toHaveAttribute('open','');await expect(protocol.locator('details')).toHaveAttribute('open','');
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await execution.getByText('查看程序执行详情',{exact:true}).click();await expect(execution.getByText(/正常结束 3 次/)).not.toBeVisible();
});
