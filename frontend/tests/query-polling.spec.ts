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

test('batch monitor backs off, honors retry-after, retains data and pauses hidden documents',async({page})=>{
 let reads=0,fail=false,retryAfter='';
 await page.clock.install({time:new Date('2026-01-01T00:00:00Z')});
 await page.clock.pauseAt(new Date('2026-01-01T00:00:01Z'));
 await page.addInitScript(()=>{sessionStorage.setItem('eval-token','test');Math.random=()=>0;});
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(p==='auth/me')data={id:'u',username:'User',role:'user'};
  if(p==='suites/manage')data={items:[],total:0};
  if(p==='batches/b/monitor'){
   reads++;
   if(fail)return route.fulfill({status:503,headers:retryAfter?{'Retry-After':retryAfter}:{},json:{detail:'database busy; retry shortly',reason:'request_budget'}});
   data={id:'b',title:'保留上次结果',cases:[],total:0,finished:0,max_concurrent:3,total_episodes:0,summary:{states:{},completed_episodes:0}};
  }
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/batches/b');await expect(page.getByRole('heading',{name:'保留上次结果'})).toBeVisible();expect(reads).toBe(1);
 fail=true;await page.clock.runFor(5000);await expect(page.getByRole('alert')).toContainText('request_budget');expect(reads).toBe(2);
 await expect(page.getByRole('heading',{name:'保留上次结果'})).toBeVisible();
 await page.clock.runFor(9999);expect(reads).toBe(2);await page.clock.runFor(1);await expect.poll(()=>reads).toBe(3);
 await page.clock.runFor(19999);expect(reads).toBe(3);retryAfter='40';await page.clock.runFor(1);await expect.poll(()=>reads).toBe(4);
 await page.clock.runFor(39999);expect(reads).toBe(4);fail=false;await page.clock.runFor(1);await expect(page.getByRole('alert')).toHaveCount(0);expect(reads).toBe(5);
 await page.clock.runFor(5000);await expect.poll(()=>reads).toBe(6);
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{value:true,configurable:true});document.dispatchEvent(new Event('visibilitychange'));});
 await page.clock.runFor(60000);expect(reads).toBe(6);
 await page.evaluate(()=>{Object.defineProperty(document,'hidden',{value:false,configurable:true});document.dispatchEvent(new Event('visibilitychange'));});
 await expect.poll(()=>reads).toBe(7);
});

test('API preserves retry metadata for JSON and non-JSON failures and aborts fetch',async({page})=>{
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname;
  if(p.endsWith('/budget-test'))return route.fulfill({status:503,headers:{'Retry-After':'12'},json:{detail:'busy',reason:'lock_timeout'}});
  if(p.endsWith('/html-test'))return route.fulfill({status:503,headers:{'Retry-After':new Date(Date.now()+20000).toUTCString()},body:'unavailable'});
  await route.fulfill({json:[]});
 });
 await page.goto('/');
 const result=await page.evaluate(async()=>{
  const {api,ApiError}=await import('/src/api.ts');const results:any[]=[];
  for(const path of ['budget-test','html-test'])try{await api(path);}catch(e){if(e instanceof ApiError)results.push({status:e.status,reason:e.reason,delay:e.retryAfterMs});}
  const c=new AbortController();c.abort();try{await api('abort-test','GET',undefined,{},c.signal);}catch(e){results.push({aborted:(e as Error).name==='AbortError'});}
  return results;
 });
 expect(result[0]).toEqual({status:503,reason:'lock_timeout',delay:12000});expect(result[1].status).toBe(503);expect(result[1].delay).toBeGreaterThan(15000);expect(result[2].aborted).toBe(true);
});

test('switching batches cancels a slow old monitor and never replaces the new batch',async({page})=>{
 let release!:()=>void;const held=new Promise<void>(resolve=>release=resolve);let oldStarted=false;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(p==='auth/me')data={id:'u',username:'User',role:'user'};
  if(p==='suites/manage')data={items:[],total:0};
  if(p==='batches/old/monitor'){oldStarted=true;await held;}
  if(p.endsWith('/monitor'))data={id:p.split('/')[1],title:p.includes('/old/')?'old batch':'new batch',cases:[],total:0,finished:0,max_concurrent:1,total_episodes:0,summary:{states:{}}};
  await route.fulfill({json:data});
 });
 try{
  await page.goto('/#/cs/batches/old');await expect.poll(()=>oldStarted).toBe(true);
  await page.evaluate(()=>location.hash='#/cs/batches/new');await expect(page.getByRole('heading',{name:'new batch'})).toBeVisible();
  release();await expect(page.getByRole('heading',{name:'old batch'})).toHaveCount(0);await expect(page.getByRole('heading',{name:'new batch'})).toBeVisible();
 }finally{release();await page.unrouteAll({behavior:'wait'});}
});
