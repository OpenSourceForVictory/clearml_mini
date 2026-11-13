import {test,expect} from '@playwright/test';
test('register both template kinds with Docker and host execution',async({page})=>{
 const sent:any[]=[];await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
 if(path==='auth/me')data={username:'admin',role:'admin'};
 if(path.endsWith('-templates')&&route.request().method()==='POST'){const body=route.request().postDataJSON();sent.push({path,...body});data={id:'new',...body};}
 await route.fulfill({json:data})});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'运行模板管理'}).click();
 for(const kind of ['客户端','服务端']){
  await page.getByRole('tab',{name:kind+'模板',exact:true}).click();
  for(const mode of ['docker','host']){
   await page.getByRole('button',{name:'注册模板',exact:true}).click();await page.getByLabel('模板名称',{exact:true}).fill(kind+'-'+mode);await page.getByLabel('版本',{exact:true}).fill('v1');await page.getByLabel('执行方式',{exact:true}).selectOption(mode);
   if(mode==='docker')await page.getByLabel('Docker 镜像').fill('test:1');else{await expect(page.getByLabel('Docker 镜像')).toHaveCount(0);await page.getByLabel('节点环境名称').fill('simulation')}
   if(kind==='客户端')await page.getByLabel('协议接入方式').selectOption('sdk-command');
   await page.getByRole('button',{name:'注册此版本'}).click();await expect(page.getByRole('button',{name:'注册此版本'})).toHaveCount(0);
  }
 }
 expect(sent).toHaveLength(4);expect(sent[1]).toMatchObject({execution_mode:'host',integration_mode:'sdk-command',host_runtime:{environment_name:'simulation'}});expect(sent[1].image).toBeUndefined();expect(sent[2]).toMatchObject({path:'admin/server-templates',port:8000,health_type:'http'});
});

test('cloning a typed template preserves its parameter schema',async({page})=>{
 const schema={version:1,fields:{scene:{type:'string',default:'room'}}};let sent:any;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(p==='auth/me')data={username:'admin',role:'admin'};
  if(p==='client-templates')data=[{id:'typed',name:'typed',version:'1',image:'image:1',command:['python3','business.py'],protocols:['http'],enabled:true,execution_mode:'docker',integration_mode:'direct',episode_reporting:true,runtime:{environment:{},overridable_env:[],assets:{}},parameter_schema:schema}];
  if(p==='admin/client-templates'&&route.request().method()==='POST'){sent=route.request().postDataJSON();data={id:'copy',...sent};}
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'运行模板管理'}).click();
 await page.getByRole('button',{name:'复制新版本'}).click();await page.getByLabel('版本',{exact:true}).fill('2');await page.getByRole('button',{name:'注册此版本'}).click();
 await expect.poll(()=>sent?.parameter_schema).toEqual(schema);
});

test('typed editor rejects duplicates and keeps drafts across mode changes',async({page})=>{
 let sent:any;let submits=0;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={username:'admin',role:'admin'};
  if(path==='admin/client-templates'&&route.request().method()==='POST'){submits++;sent=route.request().postDataJSON();data={id:'new',...sent};}
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'运行模板管理'}).click();await page.getByRole('button',{name:'注册模板',exact:true}).click();
 await page.getByLabel('模板名称',{exact:true}).fill('typed-ui');await page.getByLabel('版本',{exact:true}).fill('1');await page.getByLabel('Docker 镜像').fill('test:1');
 await page.getByLabel('容器内工作目录',{exact:true}).fill('/workspace/task');
 await page.getByLabel('启用类型化业务参数').check();await page.getByRole('button',{name:'添加业务字段'}).click();
 const first=page.getByRole('group',{name:/^业务字段 1/});
 await first.getByLabel('字段名称',{exact:true}).fill('scene');await first.getByLabel('必填',{exact:true}).check();
 await first.getByLabel('传递方式').selectOption('argv');await first.getByLabel('绑定名称').fill('--scene');
 await page.getByRole('button',{name:'添加业务字段'}).click();
 const second=page.getByRole('group',{name:/^业务字段 2/});
 await second.getByLabel('字段名称',{exact:true}).fill('scene');
 await page.getByRole('button',{name:'注册此版本'}).click();
 await expect(page.getByRole('alert')).toContainText('名称重复');expect(submits).toBe(0);
 await second.getByLabel('字段名称',{exact:true}).fill('difficulty');await second.getByLabel('参数类型').selectOption('integer');
 await second.getByLabel('设置默认值').check();await second.getByLabel('默认值（JSON）',{exact:true}).fill('2');
 await second.getByLabel('最小值').fill('1');await second.getByLabel('最大值').fill('5');await second.getByLabel('允许运行时覆盖').check();
 await second.getByLabel('传递方式').selectOption('env');await second.getByLabel('绑定名称').fill('DIFFICULTY');
 await page.getByLabel('执行方式',{exact:true}).selectOption('host');await page.getByLabel('节点环境名称').fill('simulation');await page.getByLabel('执行方式',{exact:true}).selectOption('docker');await page.getByLabel('Docker 镜像').fill('test:1');
 await expect(first.getByLabel('字段名称',{exact:true})).toHaveValue('scene');await expect(page.getByLabel('容器内工作目录',{exact:true})).toHaveValue('/workspace/task');
 await page.getByRole('button',{name:'注册此版本'}).click();
 await expect.poll(()=>submits).toBe(1);
 expect(sent.parameter_schema.fields).toMatchObject({scene:{type:'string',required:true,binding:{target:'argv',name:'--scene'}},difficulty:{type:'integer',default:2,min:1,max:5,overridable:true,binding:{target:'env',name:'DIFFICULTY'}}});
 expect(sent.runtime.work_dir).toBe('/workspace/task');
});
