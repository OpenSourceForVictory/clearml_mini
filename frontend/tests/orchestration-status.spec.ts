import {test,expect} from '@playwright/test';
test('owned batch shows task service generation, logs and revisioned stop',async({page})=>{
 const calls:any[]=[];
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='suites/manage')data={items:[],total:0};
  if(path==='auth/me')data={id:'u',username:'User',role:'user'};
  if(path==='batches/b/monitor')data={id:'b',title:'Managed',orchestrated:true,cases:[],total:1,finished:0,max_concurrent:1,total_episodes:0,control_revision:0,summary:{states:{RESERVED:1},completed_episodes:0,successes:0,goal_failures:0,episode_errors:0,execution_failures:0,unreported_tasks:0,success_rate:null}};
  if(path==='orchestrations/b')data={cases:[{case_id:'scene',endpoint:'http://node:19000',service_key:'model',service_execution_id:'generation-1',waiting_service:true}],orchestration:{service_mode:'managed',stop_after_batch:true,definitions:[{key:'model',mode:'shared'}],services:[{id:'s',name:'Model service',placement:{id:'npu-slot',node_name:'server-npu',accelerator_type:'npu',gpu_index:3,gpu_model:'Ascend'},state:'STARTING',endpoint:'http://node:19000',revision:3,cleanup_pending:true,probe:{kind:'http',error_code:'authentication_failed',phase:'response',success_threshold:1,failure_threshold:3,failures:3}}]}};
  if(path==='orchestrations/b/services/s/logs')data={content:'model loaded',job_id:'job'};
  if(path==='orchestrations/b/services/s/control'){calls.push(route.request().postDataJSON());data={};}
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/batches/b');

 await expect(page.getByRole('region',{name:'服务端启动状态'})).toContainText('server-npu / NPU 3');
 await page.getByRole('button',{name:'服务端与关联',exact:true}).click();
 const panel=page.getByRole('region',{name:'客户端与服务关系'});
 await expect(panel).toContainText('server-npu / NPU 3');await expect(panel).toContainText('型号：Ascend');
 await expect(panel).toContainText('generation-1');await expect(panel).toContainText('等待服务健康');
 await panel.getByRole('button',{name:'查看 Model service 日志'}).click();await expect(panel).toContainText('model loaded');
 await panel.getByRole('button',{name:'停止 Model service'}).click();await expect.poll(()=>calls.length).toBe(1);
 expect(calls[0]).toEqual({action:'stop',expected_revision:3});await expect(panel.getByRole('button',{name:'恢复 Model service'})).toBeDisabled();
 await panel.locator('summary',{hasText:'就绪检查详情'}).click();
 await expect(panel).toContainText('认证失败，请检查节点凭据引用');
 await expect(panel).toContainText('节点凭据需管理员处理');
 await expect(page.getByRole('button',{name:'管理设置',exact:true})).toHaveCount(0);
});

test('default task page shows server scheduling blockers and then startup status',async({page})=>{
 let phase:'pending'|'starting'='pending',fail=false;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'User',role:'user'};
  if(path==='suites/manage')data={items:[],total:0};
  if(path==='batches/b/monitor')data={id:'b',title:'Managed',orchestrated:true,cases:[],total:1,finished:0,max_concurrent:1,total_episodes:0,control_revision:0,summary:{states:{PENDING:1},completed_episodes:0,successes:0,goal_failures:0,episode_errors:0,execution_failures:0,unreported_tasks:0,success_rate:null}};
  if(path==='orchestrations/b'){
   if(fail){await route.abort();return;}
   data={cases:[],orchestration:{service_mode:'managed',stop_after_batch:true,definitions:[{key:'model',mode:'shared'}],
    services:phase==='starting'?[{id:'s',service_key:'model',name:'Model service',placement:{id:'npu-slot',node_name:'server-npu',accelerator_type:'npu',gpu_index:3,gpu_model:'Ascend'},state:'STARTING',endpoint:'http://node:19000',revision:1,cleanup_pending:false}]:[],
    scheduling:phase==='pending'?[{key:'model',pool_name:'server-pool',status:'needs_environment',reasons:[],nodes:[{node_name:'server-1',gpu_index:0,ready:false,reasons:[{code:'storage_pending',message:'工作目录配置未生效或报告已过期'}]}]}]:[]}};
  }
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/batches/b');
 const summary=page.getByRole('region',{name:'服务端启动状态'});
 await expect(summary).toContainText('服务端环境未就绪');
 await expect(summary).toContainText('server-pool');
 await summary.getByText('查看服务端候选设备与阻塞原因').click();
 await expect(summary).toContainText('工作目录配置未生效或报告已过期');
 phase='starting';
 await expect(summary).toContainText('正在启动或等待健康检查');
 await expect(summary).not.toContainText('服务端环境未就绪');
 fail=true;
 await expect(summary.getByRole('alert')).toContainText('服务端状态更新失败');
 fail=false;
 await expect(summary.getByRole('alert')).toHaveCount(0);
 await summary.getByRole('button',{name:'查看服务端与关联详情'}).click();
 await expect(page.getByRole('region',{name:'客户端与服务关系'})).toContainText('Model service');
});


test('explicit replica target shows partial scheduling and both distinct instances',async({page})=>{
 let count=1,logsFail=true;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(p==='auth/me')data={id:'u',username:'User',role:'user'};
  if(p==='batches/b/monitor')data={id:'b',title:'Replicas',orchestrated:true,cases:[],total:1,finished:0,max_concurrent:1,total_episodes:0,control_revision:0,summary:{states:{RUNNING:1},completed_episodes:0,successes:0,goal_failures:0,episode_errors:0,execution_failures:0,unreported_tasks:0,success_rate:null}};
  if(p==='orchestrations/b')data={cases:[],orchestration:{service_mode:'managed',stop_after_batch:true,definitions:[{key:'model',mode:'shared',instance_count:2}],services:Array.from({length:count},(_,i)=>({id:'replica-'+i,name:'Same service name',service_key:'model',state:i?'STARTING':'HEALTHY',endpoint:'http://server:'+String(19000+i),revision:0,cleanup_pending:false,placement:{node_name:i?'server-gpu':'server-npu',gpu_index:i?3:0,accelerator_type:i?'gpu':'npu',gpu_model:i?'GPU model':'Ascend'}})),scheduling:count===1?[{key:'model',pool_name:'npu',status:'waiting_resources',reasons:[{code:'global_concurrency',message:'平台活动任务已达上限'}],nodes:[]}]:[]}};
  if(p.endsWith('/replica-0/logs'))data={content:'first instance log'};
  if(p.endsWith('/replica-1/logs')){
   if(logsFail){await route.fulfill({status:500,json:{detail:'read failed'}});return;}
   data={content:'second instance log'};
  }
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/batches/b');const summary=page.getByRole('region',{name:'服务端启动状态'});
 await expect(summary).toContainText('目标 2 · 已创建 1 · 就绪 1');await expect(summary).toContainText('未创建 1');await expect(summary).toContainText('平台活动任务已达上限');
 count=2;await expect(summary).toContainText('目标 2 · 已创建 2 · 就绪 1 · 启动中 1');await expect(summary).not.toContainText('平台活动任务已达上限');
 await summary.getByRole('button',{name:'查看服务端与关联详情'}).click();const detail=page.getByRole('region',{name:'客户端与服务关系'});
 await expect(detail).toContainText('实例 ID：replica-0');await expect(detail).toContainText('实例 ID：replica-1');const first=detail.getByRole('row',{name:'服务实例 replica-0',exact:true}),second=detail.getByRole('row',{name:'服务实例 replica-1',exact:true});
 await expect(first).toContainText('server-npu / NPU 0');await expect(first).not.toContainText('GPU 3');
 await expect(second).toContainText('server-gpu / GPU 3');await expect(second).not.toContainText('NPU 0');
 await first.getByRole('button',{name:'查看 Same service name 日志'}).click();
 await second.getByRole('button',{name:'查看 Same service name 日志'}).click();
 const firstLogs=detail.getByRole('region',{name:'实例 replica-0 日志'}),secondLogs=detail.getByRole('region',{name:'实例 replica-1 日志'});
 await expect(firstLogs).toContainText('first instance log');await expect(secondLogs.getByRole('alert')).toContainText('read failed');
 logsFail=false;await secondLogs.getByRole('button',{name:'刷新日志'}).click();
 await expect(secondLogs).toContainText('second instance log');await expect(firstLogs).not.toContainText('second instance log');
 await firstLogs.getByRole('button',{name:'关闭服务日志'}).click();await expect(firstLogs).toHaveCount(0);await expect(secondLogs).toBeVisible();
});
