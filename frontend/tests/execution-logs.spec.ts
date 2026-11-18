import {test,expect} from '@playwright/test';

test('service logs show gaps, append by cursor, stop at completion and close',async({page})=>{
 let calls=0,complete=false;
 const cursors:string[]=[];
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const url=new URL(route.request().url()),path=url.pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'User',role:'user'};
  if(path==='suites/manage')data={items:[],total:0};
  if(path==='batches/b/monitor')data={id:'b',title:'Logs',orchestrated:true,cases:[],total:0,finished:0,max_concurrent:1,total_episodes:0,control_revision:0,summary:{states:{},completed_episodes:0,successes:0,goal_failures:0,episode_errors:0,execution_failures:0,unreported_tasks:0,success_rate:null}};
  if(path==='orchestrations/b')data={cases:[],orchestration:{definitions:[],services:[{id:'s',name:'Server',current_job_id:'j',state:'RUNNING',revision:0,cleanup_pending:false}]}};
  if(path==='orchestrations/b/services/s/logs'){
   cursors.push(url.searchParams.get('after')||'');calls++;
   data={run_id:'r',chunks:[{sequence:calls,content:'line-'+calls,stream:'stderr'}],next_after:calls,gap:calls===1,truncated:true,complete};
  }
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/batches/b');
 await page.getByRole('button',{name:'服务端与关联',exact:true}).click();
 await page.getByRole('button',{name:'查看 Server 日志'}).click();
 const panel=page.getByRole('region',{name:'实例 s 日志'});
 await expect(panel).toContainText('[stderr] line-1');
 await expect(panel).toContainText('部分日志已淘汰');
 complete=true;
 await expect.poll(()=>calls).toBe(2);
 expect(cursors).toEqual(['-1','1']);
 await page.waitForTimeout(2300);expect(calls).toBe(2);
 await panel.getByRole('button',{name:'刷新日志'}).click();
 await expect.poll(()=>calls).toBe(3);expect(cursors[2]).toBe('-1');
 await panel.getByRole('button',{name:'关闭服务日志'}).click();
 await expect(panel).toHaveCount(0);
 await page.waitForTimeout(2200);expect(calls).toBe(3);
});

test('replaced service attempt rejects late logs and retries a failed read',async({page})=>{
 let job='old',fail=true,release:(()=>void)|undefined;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const url=new URL(route.request().url()),path=url.pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'User',role:'user'};
  if(path==='suites/manage')data={items:[],total:0};
  if(path==='batches/b/monitor')data={id:'b',title:'Logs',orchestrated:true,cases:[],total:0,finished:0,max_concurrent:1,total_episodes:0,control_revision:0,summary:{states:{},completed_episodes:0,successes:0,goal_failures:0,episode_errors:0,execution_failures:0,unreported_tasks:0,success_rate:null}};
  if(path==='orchestrations/b')data={cases:[],orchestration:{definitions:[],services:[{id:'s',name:'Server',current_job_id:job,state:'RUNNING',revision:0,cleanup_pending:false}]}};
  if(path==='orchestrations/b/services/s/logs'){
   if(url.searchParams.get('job_id')==='old'){
    await new Promise<void>(resolve=>{release=resolve;});
    data={run_id:'old-run',chunks:[{content:'STALE CONTENT'}],next_after:1,complete:true};
   }else{
    if(fail){await route.fulfill({status:503,body:'unavailable'});return;}
    data={run_id:'new-run',chunks:[{content:'LATEST CONTENT'}],next_after:1,complete:true};
   }
  }
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/batches/b');
 await page.getByRole('button',{name:'服务端与关联',exact:true}).click();
 await page.getByRole('button',{name:'查看 Server 日志'}).click();
 await expect.poll(()=>Boolean(release)).toBeTruthy();job='new';
 const panel=page.getByRole('region',{name:'实例 s 日志'});
 await expect(panel.getByRole('alert')).toContainText('503');
 release!();fail=false;
 await panel.getByRole('button',{name:'刷新日志'}).click();
 await expect(panel).toContainText('LATEST CONTENT');
 await expect(panel).not.toContainText('STALE CONTENT');
});
