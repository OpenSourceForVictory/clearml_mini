import {test,expect} from '@playwright/test';
test('server machine shows deploy, status, logs and revisioned stop',async({page})=>{
 let operation:any;await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{let data:any=[];const p=new URL(route.request().url()).pathname.replace('/api/v1/','');
 if(p==='auth/me')data={username:'admin',role:'admin'};
 if(p==='admin/nodes')data=[{id:'n',node_name:'server',roles:['server'],enabled:true,status:'online'}];
 if(p==='admin/services')data=[{id:'s',name:'inference',worker_id:'n',state:'HEALTHY',online:true,revision:4,desired_running:true,cleanup_pending:true,endpoint:'http://192.168.8.151:19093',template:{name:'demo',version:'1'}}];
 if(p==='admin/services/s/logs')data={content:'ready for requests',history:[]};
 if(p==='admin/services/s/control'){operation=route.request().postDataJSON();data={}};
 await route.fulfill({json:data})});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'服务端机器'}).click();
 await expect(page.getByRole('heading',{name:'推理服务实例'})).toBeVisible();await expect(page.getByText('健康',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'日志',exact:true}).click();await expect(page.getByText('ready for requests')).toBeVisible();
 await page.getByRole('button',{name:'停止',exact:true}).click();await expect.poll(()=>operation).toEqual({action:'stop',expected_revision:4});
 await expect(page.getByRole('button',{name:'启动新执行'})).toBeDisabled();
});

test('service deployment sends typed business values separately from environment',async({page})=>{
 let submitted:any;await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(p==='auth/me')data={username:'admin',role:'admin'};
  if(p==='admin/nodes')data=[{id:'n',node_name:'server',roles:['server'],enabled:true,status:'online'}];
  if(p==='workers')data=[{id:'slot',worker_id:'n',pool_id:'p',node_name:'server',gpu_index:0,gpu_model:'test'}];
  if(p==='admin/server-templates')data=[{id:'t',name:'typed',version:'1',enabled:true,execution_mode:'docker',parameter_schema:{version:1,fields:{scene:{type:'string',required:true}}}}];
  if(p==='admin/services'&&route.request().method()==='POST'){submitted=route.request().postDataJSON();data={id:'s'}}
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'服务端机器'}).click();
 await page.getByRole('button',{name:'部署服务实例',exact:true}).click();
 await page.getByLabel('实例名称',{exact:true}).fill('typed instance');await page.getByLabel('服务端模板版本').selectOption('t');
 await page.getByLabel('目标机器 / GPU').selectOption('slot');await page.getByLabel('服务机器 IPv4 地址').fill('192.168.8.151');
 await page.getByLabel('业务参数 JSON',{exact:true}).fill('{"scene":"room"}');
 await page.getByRole('button',{name:'提交部署',exact:true}).click();
 await expect.poll(()=>submitted).toMatchObject({template_id:'t',slot_id:'slot',parameters:{scene:'room'},parameter_overrides:{},environment:{}});
});
