import {test,expect,type Page} from '@playwright/test';
async function setup(page:Page,resourceContract?:any){
  let executionLimit=7200,limitFailure=false;
  const requests:{body:any;key:string}[]=[];let checks=0,fail:boolean|string=false,delay=0,loseResponse=false,reporting=true;let serverChecks:any[]|undefined,clientReasons:any[]|undefined;
  const suite={id:'suite',name:'Three tasks',version:'v1',template_id:'t',cases:[1,2,3].map(i=>({case_id:'case-'+i,task_name:'task-'+i,episodes:4,seed:10+i,parameters:{variant:i}}))};
  const batch=(body:any,id='batch')=>({id,title:body.title,status:'PENDING',total:body.cases.length||3,succeeded:0,finished:0,total_episodes:body.cases.reduce((n:number,c:any)=>n+c.episodes,0)||12,cases:[]});
  await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
    if(path==='execution-limits'){if(limitFailure){await route.fulfill({status:503,json:{detail:'unavailable'}});return;}data={client_execution_max_seconds:executionLimit,client_execution_default_seconds:Math.min(300,executionLimit)};}
    if(path==='auth/me')data={id:'user',username:'user',role:'user'};
    if(path==='suites/manage')data={items:[{...suite,tags:[],status:'published',task_count:3,total_episodes:12}],total:1};
    if(path==='suites/suite')data={...suite,tags:[],status:'published',description:'',source:'',revision:0,archived:false};
    if(path==='suites')data=[suite];
    if(path==='pools')data=[{id:'pool',name:'Mixed GPUs'}];
    if(path==='client-templates')data=[{id:'t',resource_requirements:resourceContract,name:'Client',version:'v2',enabled:true,protocols:['ws'],episode_reporting:true,runtime:{overridable_env:['MODE']}}];
    if(path==='orchestrations/preflight'){
      checks++;const wait=delay;const bad=fail;const body=route.request().postDataJSON();
      if(wait)await new Promise(r=>setTimeout(r,wait));
      if(bad){await route.fulfill({status:400,json:{detail:typeof bad==='string'?bad:[{reason:'client.endpoint: invalid endpoint'}]}});return;}
      data={checked_at:new Date().toISOString(),can_submit:true,status:'needs_environment',task_count:3,total_episodes:12,cases:body.suite_id?suite.cases:body.cases,
        reasons:[],nodes:[{slot_id:'s',node_name:'R8000',gpu_index:0,gpu_model:'RTX 8000',ready:false,reasons:clientReasons||[{code:'client_image',category:'environment',message:'所需镜像未批准或未就绪',action:'请管理员准备镜像'}]}],server_checks:serverChecks,note:'检查不预留资源'};
    }
    if(path==='orchestrations'&&route.request().method()==='POST'){
      const body=route.request().postDataJSON(),key=route.request().headers()['idempotency-key'];requests.push({body,key});if(loseResponse){loseResponse=false;await route.abort();return;}data=batch(body);
    }
    if(path==='batches/batch')data={id:'batch',title:'[试跑] Three tasks',status:'SUCCEEDED',cases:[{job_id:'job',status:'SUCCEEDED',episode_reporting:reporting,progress:{completed:1,successes:0},error:null}]};
    await route.fulfill({json:data});
  });
  await page.goto('/');await page.getByRole('button',{name:'业务评测向导',exact:true}).click();
  const wizard=page.getByRole('region',{name:'集合运行向导'});
  await wizard.getByLabel('选择已保存集合').selectOption('suite');await wizard.getByLabel('批次名称',{exact:true}).fill('Full run');
  await wizard.getByRole('button',{name:'下一步',exact:true}).click();
  await expect(wizard.getByLabel('客户端模板',{exact:true})).toHaveValue('t');
  await expect(wizard.getByLabel('客户端模板',{exact:true}).locator('option')).toHaveCount(2);
  await wizard.getByText('启动变量覆盖 JSON（高级编辑）',{exact:true}).click();
  await wizard.getByLabel('启动变量覆盖 JSON',{exact:true}).fill('{"MODE":"test"}');
  await wizard.getByRole('button',{name:'下一步',exact:true}).click();
  await wizard.getByLabel('推理服务地址',{exact:true}).fill('ws://inference:8000');
  await wizard.getByRole('button',{name:'下一步',exact:true}).click();
  await wizard.getByLabel('客户端资源池',{exact:true}).selectOption('pool');await wizard.getByLabel('最大并发任务数').fill('2');
  return {wizard,requests,suite,setExecutionLimit:(n:number)=>{executionLimit=n;},setLimitFailure:(v:boolean)=>{limitFailure=v;},loseNextResponse:()=>{loseResponse=true;},withoutEpisodeReporting:()=>{reporting=false;},checks:()=>checks,setFail:(x:boolean|string)=>{fail=x;},setDelay:(x:number)=>{delay=x;},setServerChecks:(x:any[])=>{serverChecks=x;},setClientReasons:(x:any[])=>{clientReasons=x;}};
}

test('preflight names client environment and per-card server blockers',async({page})=>{
 const {wizard,setServerChecks,setClientReasons}=await setup(page);
 setClientReasons([{code:'client_runtime',category:'environment',message:'客户端宿主机环境「robotwin」未就绪：该设备未启用宿主机执行',action:'请管理员检查 Worker'},{code:'client_image',category:'environment',message:'客户端容器镜像「client:1」已批准，但节点上没有该镜像',action:'请在该节点检查业务镜像'}]);
 setServerChecks([{key:'model',pool_name:'Server GPUs',status:'waiting_resources',reasons:[],nodes:[{slot_id:'server-0',node_name:'server-1',gpu_index:1,gpu_model:'H100',ready:false,reasons:[{code:'gpu_busy',category:'resource',message:'GPU 已占用或达到共享任务上限',action:'等待现有任务释放资源'}]}]}]);
 await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();
 await expect(wizard.getByText('客户端宿主机环境「robotwin」未就绪：该设备未启用宿主机执行',{exact:false})).toBeVisible();
 await expect(wizard.getByText('客户端容器镜像「client:1」已批准，但节点上没有该镜像',{exact:false})).toBeVisible();
 const server=wizard.getByRole('region',{name:'服务端 model 候选设备'});
 await expect(server).toContainText('资源池 Server GPUs');
 await expect(server).toContainText('server-1 / 设备 1');
 await expect(server).toContainText('GPU 已占用或达到共享任务上限');
 await expect(server).toContainText('等待现有任务释放资源');
});

test('draft survives navigation and option refresh; queued submit and independent trial preserve full suite',async({page})=>{
  const {wizard,requests}=await setup(page);
  await wizard.getByRole('button',{name:'上一步',exact:true}).click();
  await wizard.getByRole('button',{name:'上一步',exact:true}).click();
  await expect(wizard.getByLabel('启动变量覆盖 JSON')).toHaveValue('{"MODE":"test"}');
  await wizard.getByRole('button',{name:'下一步',exact:true}).click();
  // Static options refresh on focus; periodic batch polling no longer reloads them.
  const refreshed=page.waitForResponse(r=>r.url().includes('/client-templates'));
  await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
  await refreshed;
  await expect(wizard.getByLabel('推理服务地址',{exact:true})).toHaveValue('ws://inference:8000');
  await wizard.getByRole('button',{name:'下一步',exact:true}).click();
  await expect(wizard.getByLabel('最大并发任务数')).toHaveValue('2');
  await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();
  await expect(wizard.getByText('3 项任务，12 个回合',{exact:true})).toBeVisible();
  await expect(wizard.getByText(/所需镜像未批准/)).toBeVisible();
  await wizard.getByText('可选：先试跑一个任务、一个回合',{exact:true}).click();
  await wizard.getByLabel('试跑任务',{exact:true}).selectOption('case-2');
  await wizard.getByRole('button',{name:'提交单回合试跑',exact:true}).click();
  await expect(wizard.getByRole('region',{name:'试跑进度'})).toContainText('已完成 1/1 回合，目标成功 0');
  expect(requests[0].body).toMatchObject({suite_id:null,max_concurrent:1,cases:[{case_id:'case-2',task_name:'task-2',episodes:1,seed:12,parameters:{variant:2}}]});
  await wizard.getByRole('button',{name:'提交排队',exact:true}).click();
  await expect(wizard.getByRole('button',{name:'提交排队',exact:true})).toBeDisabled();
  expect(requests).toHaveLength(2);expect(requests[0].key).not.toBe(requests[1].key);
  expect(requests[1].body).toMatchObject({suite_id:'suite',cases:[],max_concurrent:2,client:{environment:{MODE:'test'}}});
  await page.screenshot({path:'test-results/run-wizard.png',fullPage:true});
});

test('preflight failure is actionable and rechecking allows submission',async({page})=>{
  const {wizard,setFail}=await setup(page);setFail(true);
  await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();
  await expect(wizard.getByRole('alert')).toContainText('client.endpoint');
  await expect(wizard.getByRole('button',{name:'提交排队',exact:true})).toHaveCount(0);
  setFail(false);await wizard.getByRole('button',{name:'重新检查',exact:true}).click();
  await expect(wizard.getByRole('button',{name:'提交排队',exact:true})).toBeEnabled();
  await wizard.getByRole('button',{name:'上一步',exact:true}).click();
  await wizard.getByLabel('最大并发任务数').fill('3');
  await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();
  await expect(wizard.getByText(/最大并发 3/)).toBeVisible();
});

test('capacity diagnosis from preflight is visible without node access',async({page})=>{
  const {wizard,setFail}=await setup(page);
  setFail('case-1：客户端与服务端无法同时分配；服务 default（客户端池 Mixed GPUs；服务端池 Server GPUs）：同卡不能同时容纳独占请求');
  await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();
  await expect(wizard.getByRole('alert')).toContainText('客户端池 Mixed GPUs；服务端池 Server GPUs');
  await expect(wizard.getByRole('alert')).toContainText('同卡不能同时容纳独占请求');
});


test('late inspection from a previous suite handoff is discarded',async({page})=>{
  const {wizard,setDelay,checks}=await setup(page);setDelay(1500);
  await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();
  await expect.poll(checks).toBe(1);
  const response=page.waitForResponse(r=>r.url().endsWith('/orchestrations/preflight'));
  await page.getByRole('button',{name:'评测配置',exact:true}).click();
  await page.getByRole('button',{name:'查看 Three tasks / v1',exact:true}).click();
  await page.getByRole('button',{name:'运行此集合',exact:true}).click();
  await response;
  await expect(wizard.getByLabel('批次名称',{exact:true})).toBeEnabled();
  await wizard.getByLabel('批次名称',{exact:true}).fill('New launch');
  await wizard.getByRole('button',{name:'下一步',exact:true}).click();
  await wizard.getByRole('button',{name:'下一步',exact:true}).click();
  await wizard.getByLabel('推理服务地址',{exact:true}).fill('ws://inference:8000');
  await wizard.getByRole('button',{name:'下一步',exact:true}).click();
  await wizard.getByLabel('客户端资源池',{exact:true}).selectOption('pool');
  setDelay(0);await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();
  await expect.poll(checks).toBe(2);
  await expect(wizard.getByRole('button',{name:'提交排队',exact:true})).toBeEnabled();
});


for(const nativeUUID of [true,false]) test('lost response reuses idempotency key; native UUID='+nativeUUID,async({page})=>{
  if(!nativeUUID)await page.addInitScript(()=>Object.defineProperty(crypto,'randomUUID',{value:undefined,configurable:true}));
  const {wizard,requests,loseNextResponse}=await setup(page);
  await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();loseNextResponse();
  await wizard.getByRole('button',{name:'提交排队',exact:true}).click();
  await expect(wizard.getByRole('alert')).toBeVisible();
  await wizard.getByRole('button',{name:'提交排队',exact:true}).click();
  await expect(wizard.getByRole('button',{name:'提交排队',exact:true})).toBeDisabled();
  expect(requests).toHaveLength(2);expect(requests[0]).toEqual(requests[1]);
});

test('trial without episode reporting does not claim a completed episode',async({page})=>{
  const {wizard,withoutEpisodeReporting}=await setup(page);withoutEpisodeReporting();
  await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();
  await wizard.getByText('可选：先试跑一个任务、一个回合',{exact:true}).click();
  await wizard.getByRole('button',{name:'提交单回合试跑',exact:true}).click();
  const trial=wizard.getByRole('region',{name:'试跑进度'});
  await expect(trial).toContainText('无法验证回合报告');
  await expect(trial).not.toContainText('已完成 1/1');
});

test('multiple addresses validate duplicates and preserve modes in trial and formal submission',async({page})=>{
  const {wizard,requests}=await setup(page);
  await wizard.getByRole('button',{name:'上一步',exact:true}).click();
  await wizard.getByLabel('服务地址 1 使用模式').selectOption('exclusive');
  await wizard.getByRole('button',{name:'添加推理地址',exact:true}).click();
  await wizard.getByLabel('推理服务地址 2',{exact:true}).fill('ws://inference:8000/other');
  await wizard.getByRole('button',{name:'下一步',exact:true}).click();
  await expect(wizard.getByRole('alert')).toContainText('重复');
  await wizard.getByLabel('推理服务地址 2',{exact:true}).fill('ws://second:8000');
  await wizard.getByRole('button',{name:'下一步',exact:true}).click();
  await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();
  await wizard.getByText('可选：先试跑一个任务、一个回合',{exact:true}).click();
  await wizard.getByRole('button',{name:'提交单回合试跑',exact:true}).click();
  await expect.poll(()=>requests.length).toBe(1);
  await wizard.getByRole('button',{name:'提交排队',exact:true}).click();
  await expect.poll(()=>requests.length).toBe(2);
  const expected=[{url:'ws://inference:8000',mode:'exclusive'},{url:'ws://second:8000',mode:'shared'}];
  expect(requests[0].body.client.endpoints).toEqual(expected);
  expect(requests[1].body.client.endpoints).toEqual(expected);
});

test('editing address invalidates inspection and removing a row preserves the remaining draft',async({page})=>{
 const {wizard,requests,checks}=await setup(page);
 await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();
 await expect(wizard.getByRole('button',{name:'提交排队',exact:true})).toBeEnabled();
 await wizard.getByRole('button',{name:'上一步',exact:true}).click();
 await wizard.getByRole('button',{name:'上一步',exact:true}).click();
 await wizard.getByRole('button',{name:'添加推理地址',exact:true}).click();
 await wizard.getByLabel('推理服务地址 2',{exact:true}).fill('ws://changed:9000');
 await wizard.getByRole('button',{name:'移除地址 1',exact:true}).click();
 await expect(wizard.getByLabel('推理服务地址',{exact:true})).toHaveValue('ws://changed:9000');
 await wizard.getByRole('button',{name:'下一步',exact:true}).click();
 await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();
 await expect.poll(checks).toBe(2);
 await wizard.getByRole('button',{name:'提交排队',exact:true}).click();
 await expect.poll(()=>requests.length).toBe(1);
 expect(requests[0].body.client.endpoints).toEqual([{url:'ws://changed:9000',mode:'shared'}]);
});

test('execution limit is dynamic, validates inputs and keeps user value on refresh failure',async({page})=>{
 const ctl=await setup(page),w=ctl.wizard;
 await w.getByRole('button',{name:'上一步',exact:true}).click();await w.getByRole('button',{name:'上一步',exact:true}).click();
 await expect(w).toContainText('平台上限 7200 秒');
 const timeout=w.getByLabel('每项总超时秒',{exact:true});
 await expect(timeout).toHaveAttribute('max','7200');
 await timeout.fill('7201');await w.getByRole('button',{name:'下一步',exact:true}).click();await expect(timeout).toBeVisible();
 await timeout.fill('7200');
 ctl.setLimitFailure(true);await w.getByRole('button',{name:'刷新执行时限'}).click();
 await expect(w.getByRole('alert')).toContainText('平台执行时限读取失败');
 await w.getByRole('button',{name:'下一步',exact:true}).click();await expect(timeout).toBeVisible();
 ctl.setLimitFailure(false);await w.getByRole('button',{name:'刷新执行时限'}).click();
 await expect(w).toContainText('平台上限 7200 秒');await expect(timeout).toHaveValue('7200');
 await w.getByRole('button',{name:'下一步',exact:true}).click();
 await w.getByRole('button',{name:'下一步',exact:true}).click();
 await w.getByRole('button',{name:'检查并确认',exact:true}).click();
 await expect(w).toContainText('每项总执行时限 7200 秒');
 await w.getByRole('button',{name:'提交排队',exact:true}).click();
 await expect.poll(()=>ctl.requests.length).toBe(1);
 expect(ctl.requests[0].body.client.timeout_seconds).toBe(7200);
 await expect.poll(()=>ctl.checks()).toBeGreaterThan(0);
});

test('unlimited execution keeps finite draft and positive resource reservations',async({page})=>{
 const {wizard:w,requests}=await setup(page);
 await w.getByRole('button',{name:'上一步',exact:true}).click();await w.getByRole('button',{name:'上一步',exact:true}).click();
 const timeout=w.getByLabel('每项总超时秒',{exact:true});await timeout.fill('600');
 await w.getByLabel('不限制每项总执行时长',{exact:true}).check();await expect(timeout).toBeDisabled();
 await w.getByLabel('不限制每项总执行时长',{exact:true}).uncheck();await expect(timeout).toHaveValue('600');
 await w.getByLabel('不限制每项总执行时长',{exact:true}).check();
 await w.getByRole('button',{name:'下一步',exact:true}).click();await w.getByRole('button',{name:'下一步',exact:true}).click();
 await w.getByText('高级资源约束',{exact:true}).click();
 await w.getByLabel('不限制 CPU 上限',{exact:true}).check();await w.getByLabel('不限制内存上限',{exact:true}).check();
 await w.getByRole('button',{name:'检查并确认',exact:true}).click();
 await expect(w.getByLabel('每项 CPU 核数（可选）')).toBeVisible();
 await w.getByLabel('每项 CPU 核数（可选）').fill('2');await w.getByLabel('每项内存 GiB（可选）').fill('8');
 await w.getByRole('button',{name:'检查并确认',exact:true}).click();await expect(w).toContainText('每项总执行时限 不限');
 await w.getByRole('button',{name:'提交排队',exact:true}).click();await expect.poll(()=>requests.length).toBe(1);
 expect(requests[0].body).toMatchObject({client:{timeout_seconds:0},resources:{cpus:2,ram_gb:8,cpu_unlimited:true,memory_unlimited:true}});
});


test('template limits reserve the minimum and respect locked CPU',async({page})=>{
 const contract={version:1,cpu:{minimum:1,default:2},memory:{minimum:8,default:16},accelerator:{type:'gpu',mode:'exclusive',memory:{minimum:0,default:0}},allow_overrides:['ram_gb']};
 const {wizard:w,requests}=await setup(page,contract);
 await expect(w.getByLabel(/不限制 CPU上限/)).toBeDisabled();
 await w.getByLabel(/不限制内存上限/).check();
 await expect(w.getByLabel('客户端资源 · 内存 GiB',{exact:true})).toHaveValue('8');
 await w.getByRole('button',{name:'检查并确认',exact:true}).click();
 await w.getByRole('button',{name:'提交排队',exact:true}).click();await expect.poll(()=>requests.length).toBe(1);
 expect(requests[0].body.resources).toEqual({ram_gb:8,memory_unlimited:true});
});
