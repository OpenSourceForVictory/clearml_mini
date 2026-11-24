import {test,expect} from '@playwright/test';

test('hidden service page stops polling and preserves log cursor and expanded state',async({page})=>{
 const serviceRequests:string[]=[],logCursors:string[]=[];
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const url=new URL(route.request().url()),path=url.pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'User',role:'user'};
  if(path==='suites/manage')data={items:[],total:0};
  if(path==='batches/b/monitor')data={id:'b',title:'Polling batch',orchestrated:true,cases:[],total:0,finished:0,max_concurrent:1,total_episodes:0,scheduling_paused:false,control_revision:0,summary:{states:{},completed_episodes:0,execution_failures:0}};
  if(path==='orchestrations/b'){
   serviceRequests.push(url.search);
   data={cases:[],orchestration:{service_mode:'managed',stop_after_batch:true,definitions:[{key:'model',mode:'shared'}],services:[{id:'s',name:'Model service',state:'STARTING',endpoint:'http://node:1',revision:0,cleanup_pending:true,service_key:'model'}]}};
  }
  if(path.endsWith('/logs')){
   logCursors.push(url.searchParams.get('after')||'');
   data={run_id:'run',chunks:[{content:'line '+logCursors.length+'\n'}],next_after:logCursors.length,complete:false};
  }
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/batches/b');
 await expect(page.getByRole('heading',{name:'Polling batch'})).toBeVisible();
 await page.getByRole('button',{name:'服务端与关联',exact:true}).click();
 await page.getByRole('button',{name:'查看 Model service 日志'}).click();
 await expect(page.getByText('line 1',{exact:false})).toBeVisible();
 await page.getByRole('button',{name:'运行配置',exact:true}).click();
 const services=serviceRequests.length,logs=logCursors.length;
 await page.waitForTimeout(2400);
 expect(serviceRequests.length).toBe(services);expect(logCursors.length).toBe(logs);
 await expect(page.getByRole('heading',{name:'Polling batch'})).toBeVisible();
 await page.getByRole('button',{name:'服务端与关联',exact:true}).click();
 await expect(page.getByRole('button',{name:'关闭服务日志'})).toBeVisible();
 await expect.poll(()=>logCursors.length).toBe(logs+1);
 expect(logCursors.at(-1)).not.toBe('-1');
 await expect(page.locator('pre.logs')).toContainText('line 1');
 expect(serviceRequests.every(q=>q==='?view_mode=status')).toBeTruthy();
 await page.getByRole('button',{name:'任务与结果',exact:true}).click();
 await expect(page.getByRole('region',{name:'服务端启动状态'})).toBeVisible();
 const before=serviceRequests.length;
 await page.waitForTimeout(2400);
 expect(serviceRequests.length-before).toBe(1);
});

test('batch polling uses summaries without repeatedly loading static options',async({page})=>{
 const counts:Record<string,number>={};const queries:string[]=[];let optionsFail=false;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const u=new URL(route.request().url()),p=u.pathname.replace('/api/v1/','');counts[p]=(counts[p]||0)+1;
  if(p==='batches')queries.push(u.search);
  if(p==='client-templates'&&optionsFail){await route.fulfill({status:503,json:{detail:'options unavailable'}});return;}
  await route.fulfill({json:p==='auth/me'?{id:'u',username:'User',role:'user'}:p==='suites/manage'?{items:[],total:0}:[]});
 });
 await page.goto('/#/cs');
 await expect.poll(()=>counts.batches||0).toBeGreaterThan(0);
 await expect.poll(()=>counts['client-templates']||0).toBeGreaterThan(0);
 const options=['client-templates','pools','suites'].map(k=>counts[k]);
 expect(options.every(n=>n>0)).toBeTruthy();
 const initial=counts.batches;
 await expect.poll(()=>counts.batches).toBeGreaterThan(initial);
 expect(['client-templates','pools','suites'].map(k=>counts[k])).toEqual(options);
 expect(queries.every(q=>new URLSearchParams(q).get('view')==='summary')).toBeTruthy();
 optionsFail=true;await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
 await expect(page.getByRole('alert')).toContainText('options unavailable');
 const before=counts.batches;await expect.poll(()=>counts.batches).toBeGreaterThan(before);
 await expect(page.getByRole('alert')).toContainText('options unavailable');
 optionsFail=false;await page.evaluate(()=>window.dispatchEvent(new Event('focus')));
 await expect(page.getByRole('alert')).toHaveCount(0);
});
