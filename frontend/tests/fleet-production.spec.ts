import {test,expect} from '@playwright/test';
import fs from 'node:fs';

test.skip(!process.env.FLEET_ACCEPTANCE_USERS,'requires explicitly provisioned fleet acceptance users and templates');
test('production fleet: member wizard, actual execution, logs and owner isolation',async({page,request})=>{
 test.setTimeout(180000);page.setDefaultTimeout(15000);
 const base=process.env.FLEET_ACCEPTANCE_API||'http://127.0.0.1:18000';
 const users=JSON.parse(fs.readFileSync(process.env.FLEET_ACCEPTANCE_USERS!,'utf8'));
 const login=await request.post(base+'/api/v1/auth/login',{data:users[0]});expect(login.ok()).toBeTruthy();
 const token=(await login.json()).access_token,headers={Authorization:'Bearer '+token};
 const templates=await (await request.get(base+'/api/v1/client-templates',{headers})).json();
 const client=templates.find((t:any)=>t.name==='fleet-20261116-command-docker'&&t.enabled);
 expect(client).toBeTruthy();
 const pools=await (await request.get(base+'/api/v1/pools',{headers})).json();
 const pool=pools.find((p:any)=>p.gpu_model==='*');expect(pool).toBeTruthy();
 const name='fleet-ui-'+Date.now();let batch:any;
 await page.addInitScript(t=>sessionStorage.setItem('eval-token',t),token);
 await page.goto(base);await page.getByRole('button',{name:'CS 评测批次',exact:true}).click();
 const wizard=page.getByRole('region',{name:'集合运行向导'});
 await wizard.getByLabel('批次名称',{exact:true}).fill(name);
 await wizard.getByLabel('第 1 项回合数').fill('1');
 await wizard.getByRole('button',{name:'下一步',exact:true}).click();
 await wizard.getByLabel('客户端模板',{exact:true}).selectOption(client.id);
 await wizard.getByRole('button',{name:'下一步',exact:true}).click();
 await wizard.getByLabel('服务来源').selectOption('none');
 await wizard.getByRole('button',{name:'下一步',exact:true}).click();
 await wizard.getByLabel('客户端资源池',{exact:true}).selectOption(pool.id);
 await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();
 try {
  const response=page.waitForResponse(r=>r.url().endsWith('/api/v1/orchestrations')&&r.request().method()==='POST');
  await wizard.getByRole('button',{name:/^提交(排队|评测批次)$/}).click();
  const submitted=await response;expect(submitted.status()).toBe(201);batch=await submitted.json();
  fs.mkdirSync('test-results',{recursive:true});fs.writeFileSync('test-results/fleet-production-evidence.json',JSON.stringify({batch:batch.id,title:name,owner:users[0].username}));
  await wizard.getByRole('button',{name:'查看已提交批次'}).click();
  await expect(page.getByRole('region',{name:'客户端与服务关系'})).toBeVisible();
  const other=await request.post(base+'/api/v1/auth/login',{data:users[1]});
  const otherHeaders={Authorization:'Bearer '+(await other.json()).access_token};
  expect((await request.get(base+'/api/v1/orchestrations/'+batch.id,{headers:otherHeaders})).status()).toBe(404);
  await expect.poll(async()=>{
   const detail=await (await request.get(base+'/api/v1/orchestrations/'+batch.id,{headers})).json();
   return detail.status;
  },{timeout:120000,intervals:[2000]}).toBe('SUCCEEDED');
  const detail=await (await request.get(base+'/api/v1/orchestrations/'+batch.id,{headers})).json();
  for(const c of detail.cases){
   const logs=await (await request.get(base+'/api/v1/jobs/'+c.job_id+'/logs',{headers})).json();
   expect(logs.chunks.map((x:any)=>x.content).join('')).toContain('gpu_verified');
   expect((await request.get(base+'/api/v1/jobs/'+c.job_id+'/logs',{headers:otherHeaders})).status()).toBe(404);
  }
  await page.screenshot({path:'test-results/fleet-production.png',fullPage:true});
 } finally {
  if(batch){
   const current=await (await request.get(base+'/api/v1/batches/'+batch.id+'/monitor',{headers})).json();
   if(!['SUCCEEDED','FAILED','CANCELLED','TIMED_OUT'].includes(current.status))
    await request.post(base+'/api/v1/batches/'+batch.id+'/control',{headers,data:{action:'cancel',expected_revision:current.control_revision}});
  }
 }
});
