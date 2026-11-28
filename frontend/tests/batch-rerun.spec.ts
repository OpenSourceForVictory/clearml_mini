import {test,expect,type Page} from '@playwright/test';
async function setup(page:Page,availableSuite=true){
 let fail=false,delay=0,lose=false;const requests:any[]=[];
 const template={id:'t',name:'Historical client',version:'1',execution_mode:'host',host_runtime:{episode_launch:'whole_task',overridable_env:['MODE']},enabled:true,protocols:['ws'],task_type:'evaluation',parameter_schema:{version:1,fields:{speed:{type:'integer',default:1,overridable:true}}}};
 const server={id:'s',name:'Historical server',version:'1',execution_mode:'host',enabled:true,status:'published',protocol:'ws',parameter_schema:{version:1,fields:{count:{type:'integer'}}}};
 const draft:any={schema_version:1,source:{batch_id:'old',title:'历史批次',suite_id:'changed-suite'},templates:{client:template,services:[server]},issues:[],submission:{title:'历史批次（再次运行）',pool_id:'pool',max_concurrent:2,suite_id:null,resources:{},stop_after_batch:false,client:{template_id:'t',service_mode:'managed',timeout_seconds:0,request_timeout_seconds:19,environment:{MODE:'old'}},cases:[{case_id:'a',task_name:'first',episodes:3,seed:0,parameters:{speed:2},resources:{cpus:2,ram_gb:4,cpu_unlimited:true,memory_unlimited:false},service_keys:['model']},{case_id:'b',task_name:'second',episodes:7,seed:20,parameters:{speed:5},resources:{cpus:4,ram_gb:8},service_keys:['model']}],services:[{key:'model',template_id:'s',pool_id:'pool',slot_id:'fixed-slot',mode:'shared',instance_count:2,parameters:{count:0},environment:{},resources:{cpus:2,ram_gb:4,cpu_unlimited:true,gpu_mode:'exclusive',requested_vram_gb:0,accelerator_type:'npu'}}]}};
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(p==='auth/me')data={id:'user',username:'user',role:'user'};
  if(p==='execution-limits'){await new Promise(r=>setTimeout(r,100));data={client_execution_max_seconds:7200,client_execution_default_seconds:300};}
  if(p==='client-templates')data=[template];if(p==='runtime-templates/server')data=[server];if(p==='pools')data=[{id:'pool',name:'历史池'}];
  if(p==='suites')data=availableSuite?[{id:'changed-suite',name:'原评测集合',version:'2',template_id:'t',cases:[{case_id:'current',task_name:'new-task',episodes:1,seed:99}]}]:[];
  if(p==='suites/manage')data={items:[],total:0};
  if(p==='batches')data=[{id:'old',title:'历史批次',status:'SUCCEEDED',total:2,finished:2,succeeded:2,total_episodes:10,cases:[]}];
  if(p.endsWith('/monitor'))data={id:'old',title:'历史批次',max_concurrent:2,total:2,finished:2,total_episodes:10,cases:[],summary:{states:{},completed_episodes:0,successes:0}};
  if(p.endsWith('/rerun-draft')){const wait=delay,bad=fail;await new Promise(r=>setTimeout(r,wait));if(bad){await route.fulfill({status:404,json:{detail:'batch not found'}});return;}data=draft;}
  if(p==='orchestrations/preflight'){const body=route.request().postDataJSON();requests.push({kind:'check',body});data={checked_at:new Date().toISOString(),can_submit:true,status:'ready',cases:body.cases,reasons:[],nodes:[],note:'test',effective_parameters:body.cases.map((c:any)=>({case_id:c.case_id,parameters:{...body.client.parameters,...c.parameters},sources:{}}))};}
  if(p==='orchestrations'){requests.push({kind:'submit',body:route.request().postDataJSON(),key:route.request().headers()['idempotency-key']});if(lose){lose=false;await route.abort();return;}data={id:'new',title:'new',cases:[]};}
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/results');
 return {draft,template,requests,setFail:(v:boolean)=>{fail=v;},setDelay:(v:number)=>{delay=v;},lose:()=>{lose=true;}};
}

test('list import preserves overrides, edit, fresh preflight and idempotent new submission',async({page})=>{
 const c=await setup(page);await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();
 const w=page.getByRole('region',{name:'集合运行向导'});await expect(w.getByRole('region',{name:'历史批次来源'})).toContainText('历史批次');
 await expect(w.getByLabel('选择已保存集合')).toHaveValue('changed-suite');await expect(w.getByLabel('第 2 项回合数')).toHaveValue('7');
 await w.getByText('任务 1 详细配置',{exact:true}).click();
 await w.getByLabel('第 1 项参数',{exact:true}).fill('{"speed":9}');await w.getByLabel('第 1 项任务名').click();
 await w.getByRole('button',{name:'下一步',exact:true}).click();await expect(w.getByLabel('不限制每项总执行时长')).toBeChecked();
 await expect(w.getByLabel('请求超时秒')).toHaveValue('19');await expect(w.getByLabel('启动变量覆盖 JSON')).toContainText('old');
 await w.getByRole('button',{name:'下一步',exact:true}).click();await expect(w.getByLabel('服务来源')).toHaveValue('managed');
 await expect(w.getByLabel('服务 1 实例数')).toHaveValue('2');await expect(w.getByLabel('批次结束且客户端清理完成后停止服务')).not.toBeChecked();
 await w.getByRole('button',{name:'下一步',exact:true}).click();await expect(w.getByLabel('客户端资源池')).toHaveValue('pool');
 await w.getByRole('button',{name:'检查并确认',exact:true}).click();await expect(w.getByRole('button',{name:'提交评测批次',exact:true})).toBeEnabled();
 const body=c.requests[0].body;expect(body.suite_id).toBeNull();expect(body.client.timeout_seconds).toBe(0);expect(body.cases[0].parameters.speed).toBe(9);expect(body.cases[1].parameters.speed).toBe(5);expect(body.cases[0].resources.cpu_unlimited).toBe(true);
 expect(body.services[0]).toMatchObject({instance_count:2,slot_id:'fixed-slot',parameters:{count:0},resources:{accelerator_type:'npu',cpu_unlimited:true}});
 expect(body.disconnect_tolerance_seconds).toBeNull();
 c.lose();await w.getByRole('button',{name:'提交评测批次',exact:true}).click();await expect(w.getByRole('alert')).toBeVisible();
 await w.getByRole('button',{name:'提交评测批次',exact:true}).click();await expect(w.getByRole('status')).toContainText('已提交');
 const posts=c.requests.filter(x=>x.kind==='submit');expect(posts[0].key).toBe(posts[1].key);
});

test('rerun restores finite disconnection tolerance and keeps it editable',async({page})=>{
 const c=await setup(page);c.draft.submission.disconnect_tolerance_seconds=120;
 await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();
 const w=page.getByRole('region',{name:'集合运行向导'});
 await expect(w.getByLabel('批次名称',{exact:true})).toHaveValue('历史批次（再次运行）');
 for(let i=0;i<4&&await w.getByLabel('限制断联后继续执行的时间').count()===0;i++)
   await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w.getByLabel('限制断联后继续执行的时间')).toBeChecked();
 await expect(w.getByLabel('断联容忍秒数')).toHaveValue('120');
 await w.getByLabel('断联容忍秒数').fill('180');
 await w.getByRole('button',{name:'检查并确认',exact:true}).click();
 expect(c.requests[0].body.disconnect_tolerance_seconds).toBe(180);
});

test('detail entry, failed import retry and explicit issue acknowledgement',async({page})=>{
 const c=await setup(page);await page.getByRole('button',{name:'查看批次',exact:true}).click();
 await expect(page.getByRole('region',{name:'批次详情',exact:true})).toBeVisible();
 c.setFail(true);await page.getByRole('region',{name:'批次详情',exact:true}).getByRole('button',{name:'基于此批次创建',exact:true}).click();await expect(page.getByRole('region',{name:'历史批次导入'})).toContainText('导入失败');
 c.setFail(false);c.draft.issues=[{path:'services.0.instance_count',code:'instance_count_required',message:'请填写明确的实例数',fatal:false}];c.draft.submission.services[0].instance_count=null;
 await page.getByRole('button',{name:'重试导入',exact:true}).click();const w=page.getByRole('region',{name:'集合运行向导'});
 await expect(w).toContainText('请填写明确的实例数');await w.getByRole('button',{name:'下一步',exact:true}).click();await expect(w).toContainText('平台上限 7200 秒');await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w.getByLabel('服务 1 实例数')).toHaveValue('0');await w.getByLabel('服务 1 实例数').fill('3');
 await w.getByRole('button',{name:'下一步',exact:true}).click();await w.getByRole('button',{name:'检查并确认',exact:true}).click();await expect(w.getByRole('alert')).toContainText('请先核对');expect(c.requests).toHaveLength(0);
 await w.getByRole('checkbox',{name:'我已重新选择',exact:false}).check();await w.getByRole('button',{name:'重新检查',exact:true}).click();await expect(w.getByRole('button',{name:'提交评测批次',exact:true})).toBeEnabled();
 expect(c.requests[0].body.services[0].instance_count).toBe(3);
});

test('reimport starts clean, stale response ignored and fatal issue never preflights',async({page})=>{
 const c=await setup(page);c.setDelay(250);
 await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();
 c.setDelay(0);c.setFail(true);await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();
 await expect(page.getByRole('region',{name:'历史批次导入'})).toContainText('导入失败');
 await page.waitForTimeout(350);await expect(page).toHaveURL(/cs\/results/);
 c.setFail(false);await page.getByRole('button',{name:'重试导入',exact:true}).click();
 const w=page.getByRole('region',{name:'集合运行向导'});await expect(w.getByLabel('批次名称',{exact:true})).toHaveValue('历史批次（再次运行）');
 await w.getByLabel('批次名称',{exact:true}).fill('edited');
 await page.evaluate(()=>{location.hash='/cs/results';});
 c.draft.issues=[{path:'client',code:'heterogeneous',message:'不能合并不同客户端',fatal:true}];
 await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();
 await expect(w.getByLabel('批次名称',{exact:true})).toHaveValue('历史批次（再次运行）');
 await expect(w).toContainText('无法恢复，禁止提交');
 await w.getByRole('button',{name:'下一步',exact:true}).click();await expect(w).toContainText('平台上限 7200 秒');for(let i=0;i<2;i++)await w.getByRole('button',{name:'下一步',exact:true}).click();
 await w.getByRole('button',{name:'检查并确认',exact:true}).click();await expect(w.getByRole('alert')).toContainText('不能提交');await expect(w.getByRole('alert')).toContainText('client：不能合并不同客户端');expect(c.requests).toHaveLength(0);
});

test('missing API route differs from missing batch and retry clears error',async({page})=>{
 await setup(page);
 await page.route('**/api/v1/batches/*/rerun-draft',route=>route.fulfill({status:404,json:{detail:'Not Found'}}));
 await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();
 await expect(page.getByRole('region',{name:'历史批次导入'})).toContainText('请管理员更新 API');
 await page.route('**/api/v1/batches/*/rerun-draft',route=>route.fulfill({status:404,json:{detail:'batch not found'}}));
 await page.getByRole('button',{name:'重试导入'}).click();
 await expect(page.getByRole('region',{name:'历史批次导入'})).toContainText('原批次不存在、已删除或当前账号无权访问');
 await expect(page.getByRole('region',{name:'历史批次导入'})).not.toContainText('请管理员更新 API');
});

for(const available of [true,false])test(`historical suite defaults to snapshot and supports explicit switching available=${available}`,async({page})=>{
 const c=await setup(page,available);await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();const w=page.getByRole('region',{name:'集合运行向导'}),select=w.getByLabel('选择已保存集合');
 await expect(select).toHaveValue('changed-suite');await expect(select.locator('option:checked')).toContainText(available?'原评测集合（历史运行快照）':'当前不可用');await expect(w.getByLabel('第 1 项任务名')).toHaveValue('first');await expect(w.getByLabel('第 2 项任务名')).toHaveValue('second');
 await select.selectOption('');await expect(select).toHaveValue('');await expect(w.getByLabel('第 1 项任务名')).toHaveValue('first');
 if(available){await select.selectOption('changed-suite');await expect(w.getByLabel('第 1 项任务名')).toHaveValue('first');await w.getByRole('button',{name:'使用该集合当前配置'}).click();await expect(w.getByRole('region',{name:'集合任务摘要'})).toContainText('new-task');}
 await page.evaluate(()=>{location.hash='/cs/results';});await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();await expect(w.getByLabel('第 1 项任务名')).toHaveValue('first');await expect(w.getByLabel('第 2 项任务名')).toHaveValue('second');
 await w.getByRole('button',{name:'下一步',exact:true}).click();await expect(w).toContainText('平台上限 7200 秒');await expect(w.getByLabel('客户端模板',{exact:true})).toHaveValue('t');for(let i=0;i<2;i++)await w.getByRole('button',{name:'下一步',exact:true}).click();await w.getByRole('button',{name:'检查并确认',exact:true}).click();await expect.poll(()=>c.requests.length).toBe(1);
 expect(c.requests[0].body.suite_id).toBeNull();expect(c.requests[0].body.cases.map((x:any)=>x.task_name)).toEqual(['first','second']);await expect(w.getByRole('region',{name:'任务清单确认'})).toContainText('历史运行快照');
});
test('batch with no source suite remains custom',async({page})=>{const c=await setup(page);c.draft.source.suite_id=null;await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();await expect(page.getByLabel('选择已保存集合')).toHaveValue('');await expect(page.getByRole('option',{name:/历史运行快照/})).toHaveCount(0);});
test('missing historical client is named and explicit suite-compatible selection restores navigation',async({page})=>{
 const c=await setup(page);c.draft.submission.client.template_id='gone';c.draft.templates.client={id:'gone',name:'已停用客户端',version:'old'};c.draft.issues=[{path:'client.template_id',code:'template_unavailable',message:'原模板不可用，请重新选择',fatal:false}];
 await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();const w=page.getByRole('region',{name:'集合运行向导'});await w.getByRole('button',{name:'下一步',exact:true}).click();
 const select=w.getByLabel('客户端模板',{exact:true});await expect(select).toHaveValue('gone');await expect(select.locator('option:checked')).toContainText('已停用客户端 / old');await expect(w.getByRole('region',{name:'运行模板',exact:true}).getByRole('alert')).toContainText('不在可用模板列表');
 await select.selectOption('t');await expect(w).toContainText('平台上限 7200 秒');await w.getByRole('button',{name:'下一步',exact:true}).click();await expect(w.getByLabel('服务来源')).toBeVisible();await w.getByRole('button',{name:'下一步',exact:true}).click();await w.getByRole('checkbox',{name:'我已重新选择',exact:false}).check();await w.getByRole('button',{name:'检查并确认',exact:true}).click();await expect.poll(()=>c.requests.length).toBe(1);expect(c.requests[0].body.client.template_id).toBe('t');expect(c.requests[0].body.cases[1].parameters.speed).toBe(5);
});

test('history confirmation stays by actions and edits invalidate acknowledgement',async({page})=>{
 const c=await setup(page);c.draft.issues=[{path:'services.0.instance_count',code:'instance_count_required',message:'请填写明确的实例数',fatal:false}];c.draft.submission.services[0].instance_count=null;
 await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();
 const w=page.getByRole('region',{name:'集合运行向导'}),panel=w.getByRole('region',{name:'历史配置确认'}),ack=panel.getByRole('checkbox');
 await expect(ack).toBeDisabled();await expect(panel).toContainText('第 3 步');
 await w.getByRole('button',{name:'下一步',exact:true}).click();await expect(w).toContainText('平台上限 7200 秒');await w.getByRole('button',{name:'下一步',exact:true}).click();
 await w.getByLabel('服务 1 实例数').fill('2');await expect(ack).toBeEnabled();await ack.check();
 await w.getByRole('button',{name:'下一步',exact:true}).click();await expect(ack).toBeChecked();
 await panel.getByRole('button',{name:'修改服务 1 实例数'}).click();await w.getByLabel('服务 1 实例数').fill('3');await expect(ack).not.toBeChecked();await ack.check();
 await w.getByRole('button',{name:'下一步',exact:true}).click();await w.getByRole('button',{name:'检查并确认',exact:true}).click();await expect(w.getByRole('button',{name:'提交评测批次',exact:true})).toBeEnabled();
 await ack.uncheck();await expect(w.getByRole('button',{name:'提交评测批次',exact:true})).toHaveCount(0);
 await page.setViewportSize({width:390,height:844});await panel.scrollIntoViewIfNeeded();
 const box=await panel.boundingBox();expect(box!.x).toBeGreaterThanOrEqual(0);expect(box!.x+box!.width).toBeLessThanOrEqual(390);
 expect(c.requests).toHaveLength(1);expect(c.requests[0].body.services[0].instance_count).toBe(3);
});

test('common historical client parameter is prefilled and edited through ordinary submission',async({page})=>{
 const c=await setup(page);c.draft.submission.cases[1].parameters.speed=2;
 await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();
 const w=page.getByRole('region',{name:'集合运行向导'});
 await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w.getByLabel('speed')).toHaveValue('2');
 await expect(w).toContainText('平台上限 7200 秒');
 await w.getByLabel('speed').fill('8');
 await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w.getByLabel('服务来源')).toBeVisible();
 await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w.getByLabel('客户端资源池')).toBeVisible();
 await w.getByRole('button',{name:'检查并确认',exact:true}).click();
 await expect(w.getByRole('button',{name:'提交评测批次',exact:true})).toBeEnabled();
 expect(c.requests.at(-1).body.client.parameters.speed).toBe(8);
 expect(c.requests.at(-1).body.cases.map((x:any)=>x.parameters)).toEqual([{},{}]);
 await expect(w.getByText('有效业务参数与来源')).toBeVisible();
 await w.getByRole('button',{name:'提交评测批次',exact:true}).click();
 expect(c.requests.at(-1).body.client.parameters.speed).toBe(8);
});

test('changing a historical server template clears parameters from the previous template',async({page})=>{
 const c=await setup(page);
 await page.route('**/api/v1/runtime-templates/server',route=>route.fulfill({json:[
  {id:'s',name:'Historical server',version:'1',execution_mode:'host',enabled:true,status:'published',protocol:'ws',parameter_schema:{version:1,fields:{count:{type:'integer'}}}},
  {id:'replacement',name:'Replacement server',version:'1',execution_mode:'host',enabled:true,status:'published',protocol:'ws',parameter_schema:{version:1,fields:{model:{type:'string'}}}},
 ]}));
 await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();const w=page.getByRole('region',{name:'集合运行向导'});
 await w.getByRole('button',{name:'下一步',exact:true}).click();await expect(w).toContainText('平台上限 7200 秒');await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w.getByLabel('服务 1 历史业务参数 JSON')).toHaveValue(/count/);
 await w.getByLabel('服务 1 模板',{exact:true}).selectOption('replacement');
 await expect(w.getByLabel('服务 1 历史业务参数 JSON')).toHaveCount(0);
 await w.getByLabel('model').fill('new-model');
 await w.getByRole('button',{name:'下一步',exact:true}).click();await w.getByRole('button',{name:'检查并确认',exact:true}).click();
 expect(c.requests.at(-1).body.services[0]).toMatchObject({template_id:'replacement',parameters:{model:'new-model'}});
});

test('historical server business parameters remain editable without switching templates',async({page})=>{
 const c=await setup(page);await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();
 const w=page.getByRole('region',{name:'集合运行向导'});
 await w.getByRole('button',{name:'下一步',exact:true}).click();await expect(w).toContainText('平台上限 7200 秒');await w.getByRole('button',{name:'下一步',exact:true}).click();
 const parameters=w.getByLabel('服务 1 历史业务参数 JSON');
 await parameters.fill('{"count":3}');await w.getByLabel('服务 1 实例数').click();
 await w.getByRole('button',{name:'下一步',exact:true}).click();await w.getByRole('button',{name:'检查并确认',exact:true}).click();
 await expect(w.getByRole('region',{name:'服务端配置确认'})).toContainText('3');
 expect(c.requests.at(-1).body.services[0].parameters).toEqual({count:3});
});

test('legacy output_dir business input can be changed in rerun without a typed schema',async({page})=>{
 const c=await setup(page);
 (c.template as any).parameter_schema=null;
 c.draft.submission.cases[0].parameters={output_dir:'/business/old'};
 await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();
 const w=page.getByRole('region',{name:'集合运行向导'});
 await w.getByText('任务 1 详细配置',{exact:true}).click();
 await expect(w.getByLabel('第 1 项参数',{exact:true})).toHaveValue(/\/business\/old/);
 await w.getByLabel('第 1 项参数',{exact:true}).fill('{"output_dir":"/business/new"}');
 await w.getByLabel('第 1 项任务名').click();
 await w.getByRole('button',{name:'下一步',exact:true}).click();await expect(w).toContainText('平台上限 7200 秒');
 await expect(w.getByLabel('客户端业务参数 JSON')).toHaveValue('{}');
 await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w.getByLabel('服务来源')).toBeVisible();
 await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w.getByLabel('客户端资源池')).toBeVisible();
 await w.getByRole('button',{name:'检查并确认',exact:true}).click();
 await expect(w.getByRole('button',{name:'提交评测批次',exact:true})).toBeEnabled();
 expect(c.requests.at(-1).body.cases[0].parameters).toEqual({output_dir:'/business/new'});
 await w.getByRole('button',{name:'提交评测批次',exact:true}).click();
 expect(c.requests.at(-1).body.cases[0].parameters).toEqual({output_dir:'/business/new'});
});

test('legacy output_dir can be changed once for all historical tasks',async({page})=>{
 const c=await setup(page);(c.template as any).parameter_schema=null;
 c.draft.submission.cases[0].parameters={output_dir:'/business/old',keep:1};
 c.draft.submission.cases[1].parameters={output_dir:'/business/old',keep:2};
 await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();
 const w=page.getByRole('region',{name:'集合运行向导'});
 await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w.getByLabel('客户端业务参数 JSON')).toHaveValue(/\/business\/old/);
 await w.getByLabel('客户端业务参数 JSON').fill('{"output_dir":"/business/new"}');
 await w.getByLabel('请求超时秒').click();
 await w.getByRole('button',{name:'下一步',exact:true}).click();await w.getByRole('button',{name:'下一步',exact:true}).click();
 await w.getByRole('button',{name:'检查并确认',exact:true}).click();
 expect(c.requests.at(-1).body.client.parameters).toEqual({output_dir:'/business/new'});
 expect(c.requests.at(-1).body.cases.map((x:any)=>x.parameters)).toEqual([{keep:1},{keep:2}]);
 await w.getByRole('button',{name:'提交评测批次',exact:true}).click();
 expect(c.requests.at(-1).body.client.parameters.output_dir).toBe('/business/new');
});

test('shared historical typed value is prefilled but a later single-task edit remains local',async({page})=>{
 const c=await setup(page);c.draft.submission.cases[1].parameters.speed=2;
 await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();
 const w=page.getByRole('region',{name:'集合运行向导'});
 await w.getByText('任务 1 详细配置',{exact:true}).click();
 await w.getByLabel('第 1 项参数',{exact:true}).fill('{"speed":7}');
 await w.getByLabel('第 1 项任务名').click();
 await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w.getByLabel('speed')).toHaveValue('2');
 await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w.getByLabel('服务来源')).toBeVisible();
 await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w.getByLabel('客户端资源池')).toBeVisible();
 await w.getByRole('button',{name:'检查并确认',exact:true}).click();
 await expect(w.getByRole('button',{name:'提交评测批次',exact:true})).toBeEnabled();
 expect(c.requests.at(-1).body.client.parameters.speed).toBe(2);
 expect(c.requests.at(-1).body.cases.map((x:any)=>x.parameters.speed)).toEqual([7,undefined]);
});

test('historically different task values keep ordinary per-task precedence after common edit',async({page})=>{
 const c=await setup(page);await page.getByRole('button',{name:'基于此批次创建',exact:true}).click();
 const w=page.getByRole('region',{name:'集合运行向导'});
 await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w).toContainText('平台上限 7200 秒');
 await w.getByLabel('speed').fill('8');
 await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w.getByLabel('服务来源')).toBeVisible();
 await w.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(w.getByLabel('客户端资源池')).toBeVisible();
 await w.getByRole('button',{name:'检查并确认',exact:true}).click();
 await expect(w.getByRole('button',{name:'提交评测批次',exact:true})).toBeEnabled();
 expect(c.requests[0].body.client.parameters.speed).toBe(8);
 expect(c.requests[0].body.cases.map((x:any)=>x.parameters.speed)).toEqual([2,5]);
 await expect(w.getByText('有效业务参数与来源')).toBeVisible();
});
