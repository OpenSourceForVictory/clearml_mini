import {test,expect} from '@playwright/test';
for(const width of [1440,390])test(`machine detail restores list and failed-save draft ${width}`,async({page})=>{
 await page.setViewportSize({width,height:844});
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async r=>{
  const p=new URL(r.request().url()).pathname.replace('/api/v1/','');
  if(p==='admin/nodes/n29'&&r.request().method()==='PATCH'){await r.fulfill({status:500,json:{detail:'save failed'}});return;}
  await r.fulfill({json:p==='auth/me'?{username:'admin',role:'admin'}:p==='admin/nodes'?Array.from({length:30},(_,i)=>({id:'n'+i,node_name:'node-'+i,roles:['client'],enabled:true,status:'online',management_revision:1,max_concurrent:1,cpu_capacity:8,ram_gb_capacity:16,gpu_count:1,held_executions:0})):[]});
 });
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();
 await page.getByLabel('搜索机器').fill('node-');
 const trigger=page.getByRole('row').filter({hasText:'node-29'}).getByRole('button',{name:'查看配置'});
 await trigger.scrollIntoViewIfNeeded();const before=await page.evaluate(()=>scrollY);await trigger.click();
 await expect(page.getByRole('region',{name:'机器列表',exact:true})).toBeHidden();
 await expect(page.getByRole('button',{name:'返回机器列表'})).toBeInViewport();
 await page.getByLabel('并发上限',{exact:true}).fill('7');await page.getByRole('button',{name:'保存配置',exact:true}).click();
 await expect(page.getByRole('alert')).toContainText('save failed');await expect(page.getByLabel('并发上限',{exact:true})).toHaveValue('7');
 await page.getByRole('button',{name:'返回机器列表'}).click();await expect(trigger).toBeFocused();
 await expect.poll(()=>page.evaluate(()=>scrollY)).toBeCloseTo(before,0);
 await expect(page.getByLabel('搜索机器')).toHaveValue('node-');
 await trigger.click();await expect(page.getByLabel('并发上限',{exact:true})).toHaveValue('7');
 expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 await page.getByRole('button',{name:'返回机器列表'}).click();
 page.once('dialog',d=>d.dismiss());await page.getByRole('row').filter({hasText:'node-28'}).getByRole('button',{name:'查看配置'}).click();
 await expect(page.getByRole('region',{name:'机器列表',exact:true})).toBeVisible();
});

test('collection returns to filtered list and resumes unsaved draft',async({page})=>{
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 const suite={id:'s',name:'demo',version:'1',status:'draft',revision:0,template_id:'t',template_ids:['t'],description:'',tags:[],source:'',cases:[{case_id:'x',task_name:'x',episodes:1,seed:0,parameters:{}}],task_count:1,total_episodes:1};
 await page.route('**/api/v1/**',async r=>{const p=new URL(r.request().url()).pathname.replace('/api/v1/','');await r.fulfill({json:p==='auth/me'?{username:'user',role:'user'}:p==='suites/manage'?{items:[suite],total:1}:p==='suites/s'?suite:p==='client-templates'?[{id:'t',name:'client',version:'1',enabled:true}]:[]});});
 await page.goto('/#/cs/results');await page.getByRole('tab',{name:'评测任务集合管理'}).click();
 await page.getByLabel('搜索集合').fill('demo');const trigger=page.getByRole('button',{name:'查看 demo / 1',exact:true});await trigger.click();
 await expect(page.getByLabel('搜索集合')).toBeHidden();await page.getByLabel('集合名称',{exact:true}).fill('unsaved');
 await page.getByRole('button',{name:'返回集合列表'}).click();await expect(page.getByLabel('搜索集合')).toHaveValue('demo');await expect(trigger).toBeFocused();
 await trigger.click();await expect(page.getByLabel('集合名称',{exact:true})).toHaveValue('unsaved');
});
