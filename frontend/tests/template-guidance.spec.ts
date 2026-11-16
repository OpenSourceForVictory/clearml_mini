import {test,expect,Page} from '@playwright/test';
const template={id:'existing',name:'demo',version:'1',enabled:true,image:'demo:1',execution_mode:'docker',integration_mode:'sdk-command',command:['python3','/app/episode.py'],protocols:['http'],episode_reporting:true,endpoint_exclusive:true,platform_bindings:[{source:'task.task_name',target:'argv',name:'--task'}],parameter_schema:{version:1,fields:{scene:{type:'string',default:'room',overridable:true,binding:{target:'env',name:'SCENE'}}}},runtime:{environment:{LOG_LEVEL:'info'},overridable_env:[],assets:{dataset:'/datasets'},mounts:{shared:{source:'directories:shared',target:'/shared',read_only:true,required:true}}}};
async function setup(page:Page,fail=false){
 const writes:any[]=[];await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
 if(path==='auth/me')data={username:'admin',role:'admin'};
 if(path==='client-templates')data=[template];
 if(path==='admin/server-templates')data=[{...template,id:'server',name:'server',execution_mode:'host',image:'',integration_mode:'direct',runtime:null,host_runtime:{environment_name:'demo',assets:['dataset'],environment:{},overridable_env:[]},port:8000,protocol:'http',health_type:'http',health_path:'/healthz',startup_timeout:120}];
 if(route.request().method()!=='GET'){writes.push({path,body:route.request().postDataJSON()});if(fail){await route.fulfill({status:400,json:{detail:'invalid example'}});return}data={id:'new',...route.request().postDataJSON()}}
 await route.fulfill({json:data})});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'运行模板管理'}).click();return writes;
}
test('guidance, tabs and rejected copy preserve draft; failed registration keeps input',async({page})=>{
 const writes=await setup(page,true);const tab=page.getByRole('tab',{name:'注册新模板',exact:true});await tab.focus();await page.keyboard.press('Enter');
 await page.getByLabel('模板名称',{exact:true}).fill('draft');await page.getByLabel('版本',{exact:true}).fill('2');await page.getByLabel('Docker 镜像').fill('demo:1');
 await page.getByText('查看当前模式填写示例',{exact:true}).click();await expect(page.getByText('以下是配置写法示例',{exact:false})).toBeVisible();expect(writes).toHaveLength(0);
 await page.getByRole('tab',{name:'已注册模板',exact:true}).click();page.once('dialog',d=>d.dismiss());await page.getByRole('button',{name:'复制新版本',exact:true}).click();
 await page.getByRole('tab',{name:'注册新模板',exact:true}).click();await expect(page.getByLabel('模板名称',{exact:true})).toHaveValue('draft');
 await page.getByRole('tab',{name:'服务端模板',exact:true}).click();await page.getByRole('tab',{name:'客户端模板',exact:true}).click();await expect(page.getByLabel('模板名称',{exact:true})).toHaveValue('draft');
 await page.getByRole('button',{name:'注册此版本'}).click();await expect(page.getByRole('alert')).toBeVisible();await expect(page.getByLabel('模板名称',{exact:true})).toHaveValue('draft');expect(writes).toHaveLength(1);
});
test('structured read-only view and copy retain mappings, assets and protocol restrictions',async({page})=>{
 const writes=await setup(page);await page.getByRole('button',{name:'查看配置',exact:true}).click();const view=page.getByRole('region',{name:'模板配置详情'});
 await expect(view.getByText('scene',{exact:true})).toBeVisible();await expect(view.getByText('/datasets',{exact:false}).first()).toBeVisible();expect(writes).toHaveLength(0);
 await page.screenshot({path:'test-results/template-config-desktop.png',fullPage:true});
 await page.getByRole('button',{name:'以此配置创建新版本'}).click();await page.getByLabel('版本',{exact:true}).fill('2');await page.getByRole('button',{name:'注册此版本'}).click();
 await expect.poll(()=>writes.length).toBe(1);expect(writes[0].body).toMatchObject({platform_bindings:template.platform_bindings,parameter_schema:template.parameter_schema,endpoint_exclusive:true,protocols:['http'],runtime:{assets:template.runtime.assets,mounts:template.runtime.mounts}});
 await expect(page.getByRole('tab',{name:'已注册模板',exact:true})).toHaveAttribute('aria-selected','true');
 await page.getByRole('tab',{name:'服务端模板',exact:true}).click();await page.getByRole('button',{name:'查看配置',exact:true}).click();await expect(page.getByRole('region',{name:'模板配置详情'}).getByText('/healthz',{exact:true})).toBeVisible();
});
test('choose platform and business parameters; reject duplicate targets before submit',async({page})=>{
 const writes=await setup(page);await page.getByRole('tab',{name:'注册新模板',exact:true}).click();await page.getByLabel('模板名称',{exact:true}).fill('mapped');await page.getByLabel('版本',{exact:true}).fill('1');await page.getByLabel('Docker 镜像').fill('demo:1');
 await page.getByLabel('启用类型化业务参数').check();await page.getByRole('button',{name:'添加业务字段'}).click();await page.getByLabel('字段名称',{exact:true}).fill('scene');
 await page.getByLabel('选择要传递的参数').selectOption('task.task_name');await page.getByRole('button',{name:'添加参数映射'}).click();
 await page.getByLabel('选择要传递的参数').selectOption({label:'scene'});await page.getByRole('button',{name:'添加参数映射'}).click();
 const rows=page.getByRole('group',{name:/^参数映射 /});await rows.nth(1).getByLabel('程序接收的参数名').fill('--task');await page.getByRole('button',{name:'注册此版本'}).click();await expect(page.getByRole('alert')).toContainText('映射目标重复');expect(writes).toHaveLength(0);
 await rows.nth(1).getByLabel('程序接收的参数名').fill('--scene');await page.getByRole('button',{name:'注册此版本'}).click();await expect.poll(()=>writes.length).toBe(1);
 expect(writes[0].body.platform_bindings).toEqual([{source:'task.task_name',target:'argv',name:'--task',encoding:'literal'}]);expect(writes[0].body.parameter_schema.fields.scene.binding).toMatchObject({target:'argv',name:'--scene'});
});
test('directory selection and host/server explanations work at narrow width',async({page})=>{
 await setup(page);await page.setViewportSize({width:390,height:844});await page.getByRole('tab',{name:'注册新模板',exact:true}).click();await page.getByRole('button',{name:'添加目录挂载'}).click();
 await page.getByLabel('挂载 1 来源类型').selectOption('assets');await page.getByLabel('挂载 1 逻辑名称').fill('dataset');await expect(page.getByLabel('只读挂载 1')).toBeDisabled();
 await page.screenshot({path:'test-results/template-register-mobile.png',fullPage:true});
 await page.getByLabel('执行方式',{exact:true}).selectOption('host');await expect(page.getByText('宿主机：先在机器管理中配置',{exact:false})).toBeVisible();await expect(page.getByRole('button',{name:'添加资产引用'})).toBeVisible();
 await page.getByRole('tab',{name:'服务端模板',exact:true}).click();await page.getByRole('tab',{name:'注册新模板',exact:true}).click();await expect(page.getByText('服务端持续运行并接受客户端请求',{exact:false})).toBeVisible();
 await expect(page.getByLabel('协议接入方式')).toBeHidden();
});

test('complete examples load a draft without registering automatically',async({page})=>{
 const writes=await setup(page);await page.getByRole('tab',{name:'注册新模板',exact:true}).click();await page.locator('summary:visible').filter({hasText:'从完整样例开始'}).click();
 await page.getByLabel('运行样例',{exact:true}).filter({visible:true}).selectOption('client-docker-sdk-command');await page.getByRole('button',{name:'载入样例草稿'}).click();
 await expect(page.getByLabel('模板名称',{exact:true})).toHaveValue('echo-client-docker-sdk-command');await expect(page.getByLabel('固定参数 1',{exact:true})).toHaveValue('/app/episode.py');expect(writes).toHaveLength(0);
 await page.getByRole('button',{name:'注册此版本'}).click();await expect.poll(()=>writes.length).toBe(1);expect(writes[0].body.parameter_schema.fields.scene.default).toBe('room');
});
