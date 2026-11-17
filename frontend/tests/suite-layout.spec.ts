import {test,expect} from '@playwright/test';
test('compact suite rows, collapsible configuration and unsaved draft survive navigation',async({page})=>{
 const suite={id:'s1',name:'RoboTwin '+'long-name-'.repeat(60),version:'v1',template_id:'t',tags:['demo'],status:'published',archived:false,revision:1,description:'',source:'',cases:[{case_id:'demo',task_name:'demo',episodes:3,seed:0,parameters:{}}],task_count:1,total_episodes:3,latest_run:{id:'b',title:'评测批次',status:'SUCCEEDED'}};
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'user',role:'user'};
  if(path==='client-templates')data=[{id:'t',name:'client',version:'v1',enabled:true}];
  if(path==='suites/manage')data={items:[suite],total:1};
  if(path==='suites/s1')data=suite;
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/results');await page.getByRole('tab',{name:'评测任务集合管理'}).click();
 await expect(page.getByLabel('集合名称',{exact:true})).toBeHidden();
 const row=page.getByRole('row',{name:'集合 '+suite.name+' / v1',exact:true});await expect(row).toBeVisible();
 for(const width of [1440,390]){await page.setViewportSize({width,height:1000});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);expect((await row.boundingBox())!.height).toBeLessThan(90);}
 await row.getByRole('button').click();await expect(page.getByLabel('集合名称',{exact:true})).toBeDisabled();await page.getByRole('button',{name:'收起配置'}).click();await expect(page.getByLabel('集合名称',{exact:true})).toBeHidden();await expect(row).toBeVisible();
 await page.getByRole('button',{name:'展开配置'}).click();await expect(page.getByLabel('集合名称',{exact:true})).toHaveValue(suite.name);
 await page.getByRole('button',{name:'复制为新集合'}).click();await page.getByLabel('集合名称',{exact:true}).fill('未保存的集合');
 await page.getByRole('button',{name:'收起配置'}).click();await page.getByRole('tab',{name:'运行记录',exact:true}).click();await page.getByRole('tab',{name:'评测任务集合管理'}).click();await page.getByRole('button',{name:'展开配置'}).click();await expect(page.getByLabel('集合名称',{exact:true})).toHaveValue('未保存的集合');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});

test('published collection edits allowed clients independently and prevents empty selection',async({page})=>{
 const suite={id:'s1',name:'shared suite',version:'1',template_id:'t1',template_ids:['t1'],tags:[],status:'published',archived:false,revision:0,description:'',source:'',cases:[{case_id:'one',task_name:'demo',episodes:1,seed:0,parameters:{}}],task_count:1,total_episodes:1,latest_run:null};
 const writes:any[]=[];
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'user',role:'user'};
  if(path==='client-templates')data=[{id:'t1',name:'client A',version:'1',enabled:true},{id:'t2',name:'client B',version:'1',enabled:true}];
  if(path==='suites/manage')data={items:[suite],total:1};
  if(path==='suites/s1')data=suite;
  if(path==='suites/s1/client-templates'){
   const body=route.request().postDataJSON();writes.push(body);
   Object.assign(suite,{template_id:body.template_ids[0],template_ids:body.template_ids,revision:suite.revision+1});data=suite;
  }
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/results');await page.getByRole('tab',{name:'评测任务集合管理'}).click();
 await page.getByRole('row',{name:'集合 shared suite / 1',exact:true}).getByRole('button').click();
 await expect(page.getByLabel('集合名称',{exact:true})).toBeDisabled();
 await page.getByTestId('suite-client-t2').check();
 await page.getByRole('button',{name:'保存可用客户端模板',exact:true}).click();
 await expect.poll(()=>writes.length).toBe(1);
 expect(writes[0]).toEqual({expected_revision:0,template_ids:['t1','t2']});
 await page.getByTestId('suite-client-t1').uncheck();
 await page.getByTestId('suite-client-t2').uncheck();
 await expect(page.getByRole('button',{name:'保存可用客户端模板',exact:true})).toBeDisabled();
 await page.getByTestId('suite-client-t2').check();
 await page.getByRole('button',{name:'保存可用客户端模板',exact:true}).click();
 await expect.poll(()=>writes.length).toBe(2);
 expect(writes[1]).toEqual({expected_revision:1,template_ids:['t2']});
 await page.getByRole('button',{name:'收起配置'}).click();await page.getByRole('button',{name:'展开配置'}).click();
 await expect(page.getByTestId('suite-client-t2')).toBeChecked();await expect(page.getByTestId('suite-client-t1')).not.toBeChecked();
});

test('wizard offers only collection allowed clients and permits choosing the second',async({page})=>{
 const suite={id:'s',name:'reusable',version:'1',template_id:'a',template_ids:['a','b'],cases:[{case_id:'one',task_name:'demo',episodes:1,seed:0,parameters:{}}]};
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'user',role:'user'};
  if(path==='suites')data=[suite];
  if(path==='client-templates')data=['a','b','c'].map(id=>({id,name:'Client '+id,version:'1',enabled:true,protocols:['ws']}));
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'业务评测向导',exact:true}).click();
 const wizard=page.getByRole('region',{name:'集合运行向导'});
 await wizard.getByLabel('选择已保存集合').selectOption('s');
 await wizard.getByLabel('批次名称',{exact:true}).fill('selected client');
 await wizard.getByRole('button',{name:'下一步',exact:true}).click();
 const clients=wizard.getByLabel('客户端模板',{exact:true});
 await expect(clients.locator('option')).toHaveCount(3);
 await expect(clients.locator('option[value="c"]')).toHaveCount(0);
 await clients.selectOption('b');await expect(clients).toHaveValue('b');
});
