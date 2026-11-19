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
