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
