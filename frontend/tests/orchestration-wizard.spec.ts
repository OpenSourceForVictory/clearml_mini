import {test,expect,Page} from '@playwright/test';
async function setup(page:Page){
 const requests:any[]=[];
 const schema={version:1,fields:{batch_size:{type:'integer',default:2,min:1,max:8,overridable:true},fast:{type:'boolean',default:false},model:{type:'enum',enum:['a','b'],default:'a'}}};
 const client={id:'client',name:'Client',version:'1',enabled:true,task_type:'command',command:['python','run.py'],protocols:['http'],parameter_schema:schema};
 const server={...client,id:'server',name:'Server',task_type:'evaluation',protocol:'http',status:'published',execution_mode:'host'};
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'user',username:'User',role:'user'};
  if(path==='pools')data=[{id:'pool',name:'Shared lab'}];
  if(path==='client-templates'||path==='runtime-templates/client')data=[client];
  if(path==='runtime-templates/server')data=[server];
  if(path==='suites/manage')data={items:[],total:0};
  if(path==='orchestrations/preflight'){
   const body=route.request().postDataJSON();requests.push({path,body});data={checked_at:new Date().toISOString(),can_submit:true,status:'ready',cases:body.new_suite?.cases||body.cases,reasons:[],nodes:[],note:'联合检查',effective_parameters:[{case_id:'case-001',parameters:body.client.parameters,sources:{batch_size:'batch'}}]};
  }
  if(path==='orchestrations'){requests.push({path,body:route.request().postDataJSON()});data={id:'b',title:'New run',cases:[]};}
  if(path==='runtime-templates/client/client/clone'){data={...client,...route.request().postDataJSON(),id:'copy',status:'draft',revision:0}}
  if(path==='runtime-templates/client/copy'){data={...client,...route.request().postDataJSON().definition,id:'copy',status:'draft',revision:1}}
  if(path==='runtime-templates/client/copy/actions'){data={...client,id:'copy',version:'2',status:'published',revision:2}}
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'CS 评测批次',exact:true}).click();
 const wizard=page.getByRole('region',{name:'集合运行向导'});
 await wizard.getByLabel('批次名称',{exact:true}).fill('New run');await wizard.getByLabel('第 1 项回合数').fill('1');
 await wizard.getByRole('button',{name:'下一步',exact:true}).click();await wizard.getByLabel('客户端模板',{exact:true}).selectOption('client');
 return {wizard,requests};
}

test('managed service passes separate typed parameters and automatic addressing',async({page})=>{
 const {wizard,requests}=await setup(page);
 await wizard.getByLabel('batch_size',{exact:true}).fill('4');await wizard.getByLabel('fast',{exact:true}).selectOption('true');
 await wizard.getByRole('button',{name:'下一步',exact:true}).click();await wizard.getByLabel('服务来源').selectOption('managed');
 await wizard.getByLabel('服务 1 模板').selectOption('server');await wizard.getByLabel('batch_size',{exact:true}).fill('8');
 await wizard.getByLabel('服务 1 访问模式').selectOption('exclusive');
 await wizard.getByRole('button',{name:'下一步',exact:true}).click();await wizard.getByLabel('客户端资源池',{exact:true}).selectOption('pool');
 await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();await expect(wizard.getByRole('button',{name:'提交评测批次'})).toBeEnabled();
 await wizard.getByText('有效业务参数与来源',{exact:true}).click();await expect(wizard.getByText('"batch_size": 4',{exact:false})).toBeVisible();
 await wizard.getByRole('button',{name:'提交评测批次'}).click();await expect.poll(()=>requests.length).toBe(2);
 expect(requests[1].body.client).toMatchObject({service_mode:'managed',parameters:{batch_size:4,fast:true}});
 expect(requests[1].body.services[0]).toMatchObject({key:'default',mode:'exclusive',pool_id:'pool',parameters:{batch_size:8}});
 expect(requests[1].body.services[0]).not.toHaveProperty('address');
 expect(requests[1].body.client).not.toHaveProperty('endpoints');
});

test('no-service command and atomic new suite preserve backward navigation',async({page})=>{
 const {wizard,requests}=await setup(page);
 await wizard.getByRole('button',{name:'上一步',exact:true}).click();await wizard.getByLabel('同时保存为可复用集合').check();
 await wizard.getByLabel('新集合名称').fill('Reusable');await wizard.getByRole('button',{name:'下一步',exact:true}).click();
 await wizard.getByRole('button',{name:'下一步',exact:true}).click();await wizard.getByLabel('服务来源').selectOption('none');
 await wizard.getByRole('button',{name:'下一步',exact:true}).click();await wizard.getByLabel('客户端资源池',{exact:true}).selectOption('pool');
 await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();await wizard.getByRole('button',{name:'提交评测批次'}).click();
 await expect.poll(()=>requests.length).toBe(2);expect(requests[1].body.new_suite).toMatchObject({name:'Reusable',version:'1',template_id:'client'});
 expect(requests[1].body).toMatchObject({services:[],cases:[],client:{service_mode:'none'}});
});

test('copy cancel preserves selection, published copy becomes selected',async({page})=>{
 const {wizard}=await setup(page);
 await wizard.getByRole('button',{name:'复制修改客户端模板'}).click();
 await wizard.getByLabel('新版本',{exact:true}).fill('2');await wizard.getByRole('button',{name:'取消编辑'}).click();
 await expect(wizard.getByLabel('客户端模板',{exact:true})).toHaveValue('client');
 await wizard.getByRole('button',{name:'复制修改客户端模板'}).click();await wizard.getByLabel('新版本',{exact:true}).fill('2');
 await wizard.getByRole('button',{name:'保存并发布此版本'}).click();await expect(wizard.getByLabel('客户端模板',{exact:true})).toHaveValue('copy');
});

test('narrow viewport retains all five steps',async({page})=>{
 await page.setViewportSize({width:390,height:844});const {wizard}=await setup(page);
 await expect(wizard.getByLabel('运行步骤').locator('li')).toHaveCount(5);
 await expect(wizard.getByLabel('客户端模板',{exact:true})).toBeVisible();
 await expect.poll(()=>page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
});

test('typed invalid input and version conflict retain the editing draft',async({page})=>{
 const {wizard}=await setup(page);
 await wizard.getByLabel('batch_size',{exact:true}).fill('1.5');await wizard.getByRole('button',{name:'下一步',exact:true}).click();
 await expect(wizard.getByLabel('客户端模板',{exact:true})).toBeVisible();
 await wizard.getByLabel('batch_size',{exact:true}).fill('4');await wizard.getByRole('button',{name:'复制修改客户端模板'}).click();
 await page.route('**/api/v1/runtime-templates/client/client/clone',route=>route.fulfill({status:409,json:{detail:'template name and version already exist'}}));
 await wizard.getByLabel('新版本',{exact:true}).fill('conflict');await wizard.getByRole('button',{name:'保存并发布此版本'}).click();
 await expect(wizard.getByRole('alert')).toContainText('already exist');await expect(wizard.getByLabel('新版本',{exact:true})).toHaveValue('conflict');
 await expect(wizard.getByLabel('客户端模板',{exact:true})).toHaveValue('client');
});
