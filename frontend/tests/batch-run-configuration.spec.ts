import {test,expect,type Page} from '@playwright/test';
const template={name:'客户端历史模板',version:'v7',execution_mode:'host',integration_mode:'direct',task_type:'evaluation',command:['python3','business.py'],host_runtime:{environment_name:'approved-npu',environment_source:'login',working_directory:'/business/code',episode_launch:'per_episode',episode_error_policy:'continue',environment:{LOG_LEVEL:'info'}}};
const resources={accelerator_type:'npu',required_gpu_model:null,gpu_mode:'shared',requested_vram_gb:12,cpus:2,ram_gb:8,cpu_unlimited:true,memory_unlimited:true};
async function setup(page:Page,mode='managed'){
 const ctl={requests:[] as string[],delay:false,fail:false,serviceFail:false,empty:false,legacy:false,release:()=>{}};
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');ctl.requests.push(path);let data:any=[];
  if(path==='auth/me')data={id:'u',role:'user',username:'u'};
  if(path==='suites/manage')data={items:[],total:0};
  if(path.endsWith('/monitor'))data={id:'b',title:'历史批次',suite_id:'historical-suite',orchestrated:mode==='managed',max_concurrent:3,total:2,finished:2,total_episodes:6,control_revision:0,cases:ctl.empty?[]:['a','b'].map((k,i)=>({job_id:k,case_id:'case-'+k,task_name:'task-'+k,episodes:3,seed:i,status:'SUCCEEDED',attempt:1,run_id:'r'+k,episode_reporting:true,progress:{completed:3,successes:3},reason_codes:[],config:{}})),summary:{states:{SUCCEEDED:2},completed_episodes:6,successes:6,goal_failures:0,episode_errors:0,execution_failures:0,unreported_tasks:0,success_rate:1}};
  if(path.startsWith('jobs/')&&!path.endsWith('/logs')){
   if(ctl.delay&&path==='jobs/a'){ctl.delay=false;await new Promise<void>(resolve=>{ctl.release=resolve})}
   if(ctl.fail){await route.fulfill({status:500,json:{detail:'fixture unavailable'}});return}
   const key=path.slice(5);
   data={id:key,episodes:key==='a'?3:5,pool_id:'client-pool',attempts:[{endpoint:'http://assigned:21000'}],client_spec:{task_name:'task-'+key,seed:key==='a'?0:42,template:ctl.legacy?{name:'旧模板',version:'v1'}:template,parameters:{'task-config':key==='a'?'demo_clean':'pick_cube',rounds:3},parameter_sources:ctl.legacy?undefined:{'task-config':key==='a'?'batch':'case',rounds:'template'},environment:{LOG_LEVEL:'debug'},resources:ctl.legacy?{}:resources,timeout_seconds:ctl.legacy?undefined:0,request_timeout_seconds:ctl.legacy?undefined:20,service_mode:mode,service_keys:['model'],endpoints:mode==='external'?[{url:'ws://candidate:19000',mode:'shared'}]:undefined}};
  }
  if(path==='orchestrations/b'){
   if(ctl.serviceFail){await route.fulfill({status:500,json:{detail:'service snapshot unavailable'}});return}
   data={cases:[],orchestration:{service_mode:'managed',stop_after_batch:true,services:[],definitions:[{key:'model',mode:'shared',instance_count:2,pool_id:'server-pool',slot_id:null,parameters:{model:'saved-model'},resources,environment:{SERVER_FLAG:'x'},template:{...template,name:'服务端历史模板',version:'s3',port:8000,protocol:'ws',readiness:{kind:'http',port:'business',path:'/ready',startup_timeout:300}}},{key:'legacy',mode:'exclusive',max_instances:4,template:{name:'旧服务模板',version:'s1',execution_mode:'docker',image:'old-image:1',command:['server'],health_type:'tcp',health_path:'/health',startup_timeout:120},parameters:{}}]}};
  }
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/batches/b');await expect(page.getByRole('heading',{name:'历史批次',exact:true})).toBeVisible();return ctl;
}
async function open(page:Page){await page.getByRole('button',{name:'运行配置',exact:true}).click();return page.getByRole('region',{name:'本次运行配置',exact:true})}
test('snapshots, service definitions, task parameters and unlimited reservations',async({page})=>{
 const ctl=await setup(page);ctl.requests.length=0;const panel=await open(page);
 await expect(panel).toContainText('客户端历史模板 / v7');await expect(panel).toContainText('业务账号登录 Shell');await expect(panel).toContainText('记录错误继续');await expect(panel).toContainText('business.py');
 await expect(panel).toContainText('demo_clean');await expect(panel).toContainText('本次向导公共参数');await expect(panel).toContainText('服务端历史模板 / s3');await expect(panel).toContainText('旧配置：上限 4，按需启动');
 const server=panel.getByRole('article',{name:'服务配置 model'});await expect(server).toContainText('saved-model');await expect(server).toContainText('/ready');
 const limits=panel.getByRole('region',{name:'4. 资源与执行限制'});await expect(limits).toContainText('2 核');await expect(limits).toContainText('8 GiB');await expect(limits.getByText('不限制',{exact:true})).toHaveCount(3);
 await expect(panel).toContainText('未记录节点运行快照');
 await panel.getByText('本次启动变量覆盖（展开查看）',{exact:true}).first().click();await expect(panel.getByText(/"LOG_LEVEL": "debug"/).first()).toBeVisible();
 await panel.getByLabel('查看任务配置').selectOption('b');await expect(panel).toContainText('pick_cube');await expect(panel).toContainText('任务单独配置');await expect(panel).not.toContainText('demo_clean');
 expect(ctl.requests.filter(p=>/templates\//.test(p))).toEqual([]);
 await panel.getByRole('button',{name:'查看实际服务实例、节点、加速卡与日志'}).click();await expect(page.getByRole('region',{name:'批次服务页'})).toBeVisible();
});
test('external candidates, legacy missing values and narrow layout',async({page})=>{
 const ctl=await setup(page,'external');ctl.legacy=true;const panel=await open(page);
 await expect(panel).toContainText('ws://candidate:19000');await expect(panel).toContainText('http://assigned:21000');await expect(panel.getByRole('region',{name:'4. 资源与执行限制'}).getByText('不限制',{exact:true})).toHaveCount(0);expect(ctl.requests.some(p=>p.startsWith('orchestrations/'))).toBeFalsy();
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
});
test('no-service mode does not request definitions',async({page})=>{
 const ctl=await setup(page,'none');const panel=await open(page);await expect(panel).toContainText('本任务无需服务端');expect(ctl.requests.some(p=>p.startsWith('orchestrations/'))).toBeFalsy();
});
test('late response cannot replace selection; failure retries',async({page})=>{
 const ctl=await setup(page,'none');ctl.delay=true;const panel=await open(page);await expect(panel.getByRole('status')).toContainText('正在读取任务配置');
 await panel.getByLabel('查看任务配置').selectOption('b');await expect(panel).toContainText('pick_cube');const response=page.waitForResponse(r=>r.url().endsWith('/jobs/a'));ctl.release();await response;await expect(panel).not.toContainText('demo_clean');
 ctl.fail=true;await panel.getByLabel('查看任务配置').selectOption('a');await expect(panel.getByRole('alert')).toContainText('任务配置读取失败');await expect(panel).not.toContainText('pick_cube');ctl.fail=false;await panel.getByRole('button',{name:'重试任务配置'}).click();await expect(panel).toContainText('demo_clean');
});
test('service failure retries independently',async({page})=>{
 const ctl=await setup(page);ctl.serviceFail=true;const panel=await open(page);await expect(panel.getByRole('alert')).toContainText('服务配置读取失败');await expect(panel).toContainText('客户端历史模板');ctl.serviceFail=false;await panel.getByRole('button',{name:'重试服务配置'}).click();await expect(panel).toContainText('服务端历史模板');
});
test('empty batch does not request a job',async({page})=>{
 const ctl=await setup(page,'none');ctl.empty=true;await page.reload();const panel=await open(page);await expect(panel).toContainText('此批次没有任务');expect(ctl.requests.some(p=>p.startsWith('jobs/'))).toBeFalsy();
});
test('missing historical snapshot has an explicit message',async({page})=>{
 await setup(page,'none');await page.route('**/api/v1/jobs/a',route=>route.fulfill({json:{id:'a',episodes:3,client_spec:null}}));
 const panel=await open(page);await expect(panel).toContainText('此历史任务未保存客户端运行快照');await expect(panel.getByRole('heading',{name:'2. 客户端',exact:true})).toHaveCount(0);
});
test('navigation to another batch resets the selected configuration',async({page})=>{
 await setup(page,'none');const panel=await open(page);await panel.getByLabel('查看任务配置').selectOption('b');await expect(panel).toContainText('pick_cube');
 await page.goto('/#/cs/batches/another');await page.getByRole('button',{name:'运行配置',exact:true}).click();await expect(page.getByLabel('查看任务配置')).toHaveValue('a');await expect(page.getByRole('region',{name:'本次运行配置',exact:true})).toContainText('demo_clean');
});

for(const executionMode of ['host','docker'])test(`dynamic mappings include business fields and task name in ${executionMode}`,async({page})=>{
 const ctl=await setup(page);ctl.requests.length=0;
 await page.route('**/api/v1/jobs/*',async route=>{
  const key=new URL(route.request().url()).pathname.split('/').at(-1);
  await route.fulfill({json:{id:key,episodes:3,client_spec:{task_name:'task-'+key,service_mode:'managed',parameters:{'task-config':key==='a'?'demo_clean':'pick_cube',enabled:false,count:0,labels:['a','b']},template:{...template,execution_mode:executionMode,platform_bindings:[{source:'task.task_name',target:'argv',name:'--taskname'}],parameter_schema:{version:1,fields:{'task-config':{type:'string',binding:{target:'argv',name:'--task-config'}},enabled:{type:'boolean',binding:{target:'argv',name:'--enabled',encoding:'flag'}},count:{type:'integer',binding:{target:'env',name:'COUNT'}},labels:{type:'array',binding:{target:'argv',name:'--label',encoding:'repeat'}},optional:{type:'string',binding:{target:'argv',name:'--optional'}},input:{type:'string',binding:{target:'input'}}}}}}}});
 });
 const panel=await open(page),client=panel.getByRole('region',{name:'2. 客户端'}),table=client.getByRole('table',{name:'动态参数映射'});
 await expect(table).toBeVisible();
 await expect(table.getByRole('row').filter({hasText:'task.task_name'})).toContainText('--taskname');
 await expect(table.getByRole('row').filter({hasText:'task.task_name'})).toContainText('task-a');
 await expect(table.getByRole('row').filter({hasText:'business.task-config'})).toContainText('demo_clean');
 await expect(table.getByRole('row').filter({hasText:'business.count'})).toContainText('COUNT');
 await expect(table.getByRole('row').filter({hasText:'business.count'})).toContainText('0');
 await expect(table.getByRole('row').filter({hasText:'business.enabled'})).toContainText('false');
 await expect(table.getByRole('row').filter({hasText:'business.labels'})).toContainText('逐项重复');
 await expect(table.getByRole('row').filter({hasText:'business.optional'})).toContainText('未提供值');
 await expect(table.getByRole('row').filter({hasText:'business.input'})).toContainText('输入 JSON');
 await panel.getByLabel('查看任务配置').selectOption('b');
 await expect(table).toContainText('task-b');await expect(table).toContainText('pick_cube');await expect(table).not.toContainText('demo_clean');
 expect(ctl.requests.filter(p=>/templates\//.test(p))).toEqual([]);
});
test('server business-only mappings are visible and missing platform values are not invented',async({page})=>{
 await setup(page);
 await page.route('**/api/v1/orchestrations/b',route=>route.fulfill({json:{orchestration:{definitions:[{key:'model',parameters:{model:'saved-model'},template:{...template,parameter_schema:{version:1,fields:{model:{type:'string',binding:{target:'env',name:'MODEL'}}}},platform_bindings:[{source:'platform.listen.port',target:'argv',name:'--port'}]}}]}}}));
 const panel=await open(page),table=panel.getByRole('article',{name:'服务配置 model'}).getByRole('table',{name:'动态参数映射'});
 await expect(table).toBeVisible();await expect(table.getByRole('row').filter({hasText:'business.model'})).toContainText('saved-model');
 await expect(table.getByRole('row').filter({hasText:'platform.listen.port'})).toContainText('启动时解析');
});
test('legacy missing mappings differ from explicitly empty mappings',async({page})=>{
 const ctl=await setup(page,'none');ctl.legacy=true;const panel=await open(page);
 await expect(panel).toContainText('动态参数映射未记录');
 await page.route('**/api/v1/jobs/b',route=>route.fulfill({json:{client_spec:{template:{...template,platform_bindings:[],parameter_schema:{version:1,fields:{}}},parameters:{},service_mode:'none'}}}));
 await panel.getByLabel('查看任务配置').selectOption('b');await expect(panel).toContainText('未配置动态参数映射');
});

test('business mappings display without platform bindings, including implicit input and JSON',async({page})=>{
 await setup(page,'none');
 await page.route('**/api/v1/jobs/a',route=>route.fulfill({json:{client_spec:{template:{...template,parameter_schema:{version:1,fields:{config:{type:'object',binding:{target:'argv',name:'--config',encoding:'json'}},plain:{type:'string'}}}},parameters:{config:{mode:'eef'},plain:'kept'},service_mode:'none'}}}));
 const panel=await open(page),table=panel.getByRole('table',{name:'动态参数映射'});
 await expect(table.getByRole('row').filter({hasText:'business.config'})).toContainText('--config');
 await expect(table.getByRole('row').filter({hasText:'business.config'})).toContainText('JSON 编码');
 await expect(table.getByRole('row').filter({hasText:'business.config'})).toContainText('{"mode":"eef"}');
 await expect(table.getByRole('row').filter({hasText:'business.plain'})).toContainText('输入 JSON');
 await expect(table.getByRole('row').filter({hasText:'business.plain'})).toContainText('kept');
 await page.setViewportSize({width:390,height:844});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBeTruthy();
});
