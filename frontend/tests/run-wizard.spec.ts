import {test,expect,type Page} from '@playwright/test';
async function setup(page:Page){
  const requests:{body:any;key:string}[]=[];let checks=0,fail=false,delay=0,loseResponse=false,reporting=true;
  const suite={id:'suite',name:'Three tasks',version:'v1',template_id:'t',cases:[1,2,3].map(i=>({case_id:'case-'+i,task_name:'task-'+i,episodes:4,seed:10+i,parameters:{variant:i}}))};
  const batch=(body:any,id='batch')=>({id,title:body.title,status:'PENDING',total:body.cases.length||3,succeeded:0,finished:0,total_episodes:body.cases.reduce((n:number,c:any)=>n+c.episodes,0)||12,cases:[]});
  await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
    if(path==='auth/me')data={id:'user',username:'user',role:'user'};
    if(path==='suites/manage')data={items:[{...suite,tags:[],status:'published',task_count:3,total_episodes:12}],total:1};
    if(path==='suites/suite')data={...suite,tags:[],status:'published',description:'',source:'',revision:0,archived:false};
    if(path==='suites')data=[suite];
    if(path==='pools')data=[{id:'pool',name:'Mixed GPUs'}];
    if(path==='client-templates')data=[{id:'t',name:'Client',version:'v2',enabled:true,protocols:['ws'],episode_reporting:true,runtime:{overridable_env:['MODE']}}];
    if(path==='batches/preflight'){
      checks++;const wait=delay;const bad=fail;const body=route.request().postDataJSON();
      if(wait)await new Promise(r=>setTimeout(r,wait));
      if(bad){await route.fulfill({status:400,json:{detail:[{reason:'client.endpoint: invalid endpoint'}]}});return;}
      data={checked_at:new Date().toISOString(),can_submit:true,status:'needs_environment',task_count:3,total_episodes:12,cases:body.suite_id?suite.cases:body.cases,
        reasons:[],nodes:[{slot_id:'s',node_name:'R8000',gpu_index:0,gpu_model:'RTX 8000',ready:false,reasons:[{code:'client_image',category:'environment',message:'所需镜像未批准或未就绪',action:'请管理员准备镜像'}]}],note:'检查不预留资源'};
    }
    if(path==='batches'&&route.request().method()==='POST'){
      const body=route.request().postDataJSON(),key=route.request().headers()['idempotency-key'];requests.push({body,key});if(loseResponse){loseResponse=false;await route.abort();return;}data=batch(body);
    }
    if(path==='batches/batch')data={id:'batch',title:'[试跑] Three tasks',status:'SUCCEEDED',cases:[{job_id:'job',status:'SUCCEEDED',episode_reporting:reporting,progress:{completed:1,successes:0},error:null}]};
    await route.fulfill({json:data});
  });
  await page.goto('/');await page.getByRole('button',{name:'CS 评测批次',exact:true}).click();
  const wizard=page.getByRole('region',{name:'集合运行向导'});
  await wizard.getByLabel('选择已保存集合').selectOption('suite');await wizard.getByLabel('批次名称',{exact:true}).fill('Full run');
  await wizard.getByRole('button',{name:'下一步',exact:true}).click();
  await expect(wizard.getByLabel('客户端模板',{exact:true})).toBeDisabled();
  await wizard.getByLabel('推理服务地址',{exact:true}).fill('ws://inference:8000');
  await wizard.getByLabel('启动变量覆盖 JSON',{exact:true}).fill('{"MODE":"test"}');
  await wizard.getByRole('button',{name:'下一步',exact:true}).click();
  await wizard.getByLabel('客户端资源池',{exact:true}).selectOption('pool');await wizard.getByLabel('最大并发任务数').fill('2');
  return {wizard,requests,suite,loseNextResponse:()=>{loseResponse=true;},withoutEpisodeReporting:()=>{reporting=false;},checks:()=>checks,setFail:(x:boolean)=>{fail=x;},setDelay:(x:number)=>{delay=x;}};
}

test('draft survives navigation and polling; queued submit and independent trial preserve full suite',async({page})=>{
  const {wizard,requests}=await setup(page);
  await wizard.getByRole('button',{name:'上一步',exact:true}).click();
  await expect(wizard.getByLabel('启动变量覆盖 JSON')).toHaveValue('{"MODE":"test"}');
  // Wait for an actual list refresh, rather than relying on a fixed sleep.
  await page.waitForResponse(r=>r.url().includes('/client-templates'));
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


test('late inspection from a previous suite handoff is discarded',async({page})=>{
  const {wizard,setDelay,checks}=await setup(page);setDelay(1500);
  await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();
  await expect.poll(checks).toBe(1);
  const response=page.waitForResponse(r=>r.url().endsWith('/batches/preflight'));
  await page.getByRole('tab',{name:'评测集合管理',exact:true}).click();
  await page.getByRole('button',{name:'查看 Three tasks / v1',exact:true}).click();
  await page.getByRole('button',{name:'运行此集合',exact:true}).click();
  await response;
  await expect(wizard.getByLabel('批次名称',{exact:true})).toBeEnabled();
  await wizard.getByLabel('批次名称',{exact:true}).fill('New launch');
  await wizard.getByRole('button',{name:'下一步',exact:true}).click();
  await wizard.getByRole('button',{name:'下一步',exact:true}).click();
  setDelay(0);await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();
  await expect.poll(checks).toBe(2);
  await expect(wizard.getByRole('button',{name:'提交排队',exact:true})).toBeEnabled();
});


test('lost submission response reuses the original idempotency key',async({page})=>{
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
