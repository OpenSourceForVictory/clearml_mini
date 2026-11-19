import {test,expect} from '@playwright/test';
test('attempt-specific location, copy failure and unreported history',async({page})=>{
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(p==='auth/me')data={id:'user',username:'user',role:'user'};
  if(p==='batches/a/monitor')data={id:'a',title:'目录批次',max_concurrent:1,total:1,finished:1,total_episodes:1,cases:[{job_id:'j',case_id:'case',task_name:'case',status:'FAILED',attempt:2,run_id:'r2',episodes:1,seed:0,episode_reporting:true,progress:{completed:0},reason_codes:[],waiting:null,config:{pool_name:'p',client_name:'c',client_version:'1',gpu_mode:'exclusive'}}],summary:{states:{FAILED:1},completed_episodes:0,successes:0,goal_failures:0,episode_errors:1,execution_failures:1,unreported_tasks:0}};
  if(p==='jobs/j')data={attempts:[{id:'r1',attempt:1,status:'FAILED'},{id:'r2',attempt:2,status:'FAILED'}]};
  if(p.endsWith('/execution-location'))data={run_id:p.includes('/r1/')?'r1':'r2',execution_id:p.includes('/r1/')?'execution-old':'execution-new',node_name:'node',directories:p.includes('/r1/')?[]:[{id:'d',path:'/old/runs/execution-new',scan_status:'complete',cleanup_status:null,scanned_at:'2026-11-19T00:00:00Z'}]};
  if(p.endsWith('/logs'))data={chunks:[],complete:true};
  if(p.endsWith('/episodes'))data={items:[],planned:1,progress:{}};
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/batches/a');
 await page.getByRole('button',{name:'排查任务',exact:true}).click();
 const area=page.getByRole('region',{name:'执行身份与目录'});
 await expect(area).toContainText('execution-new');
 await expect(area).toContainText('/old/runs/execution-new');
 await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async()=>{throw new Error('denied')}},configurable:true}));
 await page.getByRole('button',{name:'复制目录',exact:true}).click();
 await expect(area).toContainText('复制失败');
 await page.getByLabel('执行尝试',{exact:true}).selectOption('r1');
 await expect(area).toContainText('execution-old');
 await expect(area).toContainText('目录尚未上报');
 await expect(area).not.toContainText('/old/runs/execution-new');
 await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{value:{writeText:async()=>{}},configurable:true}));
 await page.getByRole('button',{name:'复制执行 ID',exact:true}).click();
 await expect(area).toContainText('已复制');
});

test('location remains bound to the selected attempt when an old response arrives late',async({page})=>{
 let release!:()=>void;const gate=new Promise<void>(r=>release=r);let waiting=false;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(p==='auth/me')data={id:'user',username:'user',role:'user'};
  if(p==='batches/a/monitor')data={id:'a',title:'历史目录',max_concurrent:1,total:1,finished:1,total_episodes:1,cases:[{job_id:'j',case_id:'case',task_name:'case',status:'FAILED',attempt:2,run_id:'r2',episodes:1,seed:0,episode_reporting:true,progress:{completed:0},reason_codes:[],waiting:null,config:{pool_name:'p',client_name:'c',client_version:'1',gpu_mode:'exclusive'}}],summary:{states:{FAILED:1},completed_episodes:0,successes:0,goal_failures:0,episode_errors:1,execution_failures:1,unreported_tasks:0}};
  if(p==='jobs/j')data={attempts:[{id:'r1',attempt:1,status:'FAILED'},{id:'r2',attempt:2,status:'FAILED'}]};
  if(p.endsWith('/execution-location')){
   if(p.includes('/r2/')){waiting=true;await gate;data={execution_id:'late-new',directories:[]};}
   else data={execution_id:'selected-old',node_name:'old-node',directories:[{id:'bad',path:null,scan_status:'blocked',scanned_at:'2026-11-19'}]};
  }
  if(p.endsWith('/logs'))data={chunks:[],complete:true};
  if(p.endsWith('/episodes'))data={items:[],planned:1,progress:{}};
  await route.fulfill({json:data});
 });
 try{
  await page.goto('/#/cs/batches/a');await page.getByRole('button',{name:'排查任务',exact:true}).click();
  await expect.poll(()=>waiting).toBe(true);await page.getByLabel('执行尝试',{exact:true}).selectOption('r1');
  const area=page.getByRole('region',{name:'执行身份与目录'});
  await expect(area).toContainText('selected-old');await expect(area).toContainText('目录记录不可用');
  await expect(area.getByRole('button',{name:'复制目录',exact:true})).toHaveCount(0);
  release();await expect(area).not.toContainText('late-new');
 }finally{release();await page.unrouteAll({behavior:'wait'});}
});

test('task detail reports an unavailable old API and recovers without inventing a directory',async({page})=>{
 let unavailable=true;
 const job={id:'j',title:'目录核查',status:'FAILED',owner_name:'user',model_ref:'m',episodes:1,benchmark:'mock',created_at:'2026-11-20T00:00:00Z',attempts:[{id:'old',attempt:1,status:'FAILED'},{id:'current',attempt:2,status:'FAILED',cleanup_confirmed:true}]};
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'user',username:'user',role:'user'};
  if(path==='jobs')data=[job];if(path==='jobs/j')data=job;
  if(path.endsWith('/logs'))data={chunks:[],complete:true};
  if(path.endsWith('/execution-location')){
   if(unavailable){await route.fulfill({status:404,json:{detail:'Not Found'}});return;}
   data={run_id:path.includes('/old/')?'old':'current',execution_id:path.includes('/old/')?null:'execution-current',node_name:'node',directories:[]};
  }
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'查看详情',exact:true}).click();
 const area=page.getByRole('dialog',{name:'任务详情'}).getByRole('region',{name:'执行身份与目录'});
 await expect(area).toContainText('执行目录暂不可用');await expect(area.getByRole('button',{name:'复制目录',exact:true})).toHaveCount(0);
 unavailable=false;await expect(area).toContainText('execution-current');await expect(area).toContainText('目录尚未上报');
 await page.getByLabel('执行尝试',{exact:true}).selectOption('old');await expect(area).toContainText('尚未分配');await expect(area).not.toContainText('execution-current');
});
