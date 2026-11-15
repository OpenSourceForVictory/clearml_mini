import {test,expect} from '@playwright/test';
test('owned batch shows task service generation, logs and revisioned stop',async({page})=>{
 const calls:any[]=[];
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='suites/manage')data={items:[],total:0};
  if(path==='auth/me')data={id:'u',username:'User',role:'user'};
  if(path==='batches/b/monitor')data={id:'b',title:'Managed',orchestrated:true,cases:[],total:1,finished:0,max_concurrent:1,total_episodes:0,control_revision:0,summary:{states:{RESERVED:1},completed_episodes:0,successes:0,goal_failures:0,episode_errors:0,execution_failures:0,unreported_tasks:0,success_rate:null}};
  if(path==='orchestrations/b')data={cases:[{case_id:'scene',endpoint:'http://node:19000',service_key:'model',service_execution_id:'generation-1',waiting_service:true}],orchestration:{service_mode:'managed',stop_after_batch:true,definitions:[{key:'model',mode:'shared'}],services:[{id:'s',name:'Model service',state:'STARTING',endpoint:'http://node:19000',revision:3,cleanup_pending:true}]}};
  if(path==='orchestrations/b/services/s/logs')data={content:'model loaded',job_id:'job'};
  if(path==='orchestrations/b/services/s/control'){calls.push(route.request().postDataJSON());data={};}
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/batches/b');
 const panel=page.getByRole('region',{name:'客户端与服务关系'});
 await expect(panel).toContainText('generation-1');await expect(panel).toContainText('等待服务健康');
 await panel.getByRole('button',{name:'查看 Model service 日志'}).click();await expect(panel).toContainText('model loaded');
 await panel.getByRole('button',{name:'停止 Model service'}).click();await expect.poll(()=>calls.length).toBe(1);
 expect(calls[0]).toEqual({action:'stop',expected_revision:3});await expect(panel.getByRole('button',{name:'恢复 Model service'})).toBeDisabled();
 await expect(page.getByRole('button',{name:'管理设置',exact:true})).toHaveCount(0);
});
