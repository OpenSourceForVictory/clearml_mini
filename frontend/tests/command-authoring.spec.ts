import {test,expect} from '@playwright/test';
for(const mode of ['docker','host'])test('ordinary command template registration '+mode,async({page})=>{
 let sent:any;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
 if(path==='auth/me')data={username:'admin',role:'admin'};
 if(path==='client-templates'&&sent)data=[{id:'new',enabled:true,...sent}];
 if(path==='admin/client-templates'&&route.request().method()==='POST'){sent=route.request().postDataJSON();data={id:'new',...sent};}
 await route.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'运行模板管理'}).click();await page.getByRole('button',{name:'注册模板',exact:true}).click();
 await page.getByLabel('模板名称',{exact:true}).fill('ordinary');await page.getByLabel('版本',{exact:true}).fill('v1');
 await page.getByLabel('协议接入方式').selectOption('sdk-command');await page.getByLabel('任务类型',{exact:true}).selectOption('command');
 await expect(page.getByLabel('协议接入方式')).toBeDisabled();await expect(page.getByLabel('协议接入方式')).toHaveValue('direct');await expect(page.getByRole('checkbox',{name:/业务程序逐回合/})).toHaveCount(0);
 await page.getByLabel('任务类型',{exact:true}).selectOption('evaluation');await expect(page.getByLabel('协议接入方式')).toHaveValue('sdk-command');await page.getByLabel('任务类型',{exact:true}).selectOption('command');
 await page.getByLabel('执行方式',{exact:true}).selectOption(mode);if(mode==='docker')await page.getByLabel('Docker 镜像').fill('test:1');else await page.getByLabel('节点环境名称').fill('batch');
 await page.getByRole('button',{name:'注册此版本'}).click();await expect.poll(()=>sent?.task_type).toBe('command');expect(sent).toMatchObject({integration_mode:'direct',episode_reporting:false,execution_mode:mode});
 await page.getByRole('button',{name:'查看配置',exact:true}).click();const detail=page.getByRole('region',{name:'模板配置详情'});await expect(detail.getByLabel('任务类型',{exact:true})).toHaveValue('command');await expect(detail.getByLabel('任务类型',{exact:true})).toBeDisabled();await detail.getByRole('button',{name:'以此配置创建新版本'}).click();await expect(page.getByLabel('任务类型',{exact:true})).toHaveValue('command');
});

test('command collection normalizes episodes and wizard submits without inference address',async({page})=>{
 let submitted:any,saved:any,preview:any;
 const template={id:'cmd',name:'batch-script',version:'v1',task_type:'command',enabled:true,execution_mode:'docker',integration_mode:'direct',image:'test:1',command:['python','batch.py'],protocols:['http'],episode_reporting:false};
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
 if(path==='auth/me')data={username:'member',role:'user'};
 if(path==='client-templates')data=[template];
 if(path==='pools')data=[{id:'pool',name:'CPU pool'}];
 if(path==='suites/manage')data={items:[],total:0};
 if(path==='suites/preview'){const document=JSON.parse(route.request().postDataJSON().content);preview=document;data={valid:true,errors:[],document,task_count:document.cases.length,total_episodes:0};}
 if(path==='suites'&&route.request().method()==='POST'){saved=route.request().postDataJSON();data={id:'suite',revision:0,...saved};}
 if(path==='orchestrations/preflight'){const body=route.request().postDataJSON();data={checked_at:new Date().toISOString(),can_submit:true,status:'ready',cases:body.cases,task_count:1,total_episodes:0,nodes:[],reasons:[]};}
 if(path==='orchestrations'&&route.request().method()==='POST'){submitted=route.request().postDataJSON();data={id:'batch'};}
 await route.fulfill({json:data});});
 await page.goto('/#/cs/results');await page.getByRole('tab',{name:'评测任务集合管理'}).click();await page.getByRole('button',{name:'新建集合',exact:true}).click();await page.getByLabel('集合名称',{exact:true}).fill('batch collection');await page.getByTestId('suite-client-cmd').check();await page.getByRole('button',{name:'添加任务',exact:true}).click();await page.getByLabel('任务名称 0').fill('convert');await expect(page.getByLabel('任务回合 0')).toBeDisabled();await page.getByRole('button',{name:'发布版本',exact:true}).click();await expect.poll(()=>saved?.cases[0].episodes).toBe(1);expect(preview.cases[0].episodes).toBe(1);
 await page.getByRole('button',{name:'业务评测向导',exact:true}).click();const wizard=page.getByRole('region',{name:'集合运行向导'});await wizard.getByLabel('批次名称',{exact:true}).fill('command batch');await wizard.getByRole('button',{name:'下一步',exact:true}).click();await wizard.getByLabel('客户端模板',{exact:true}).selectOption('cmd');await wizard.getByRole('button',{name:'下一步',exact:true}).click();await expect(wizard.getByLabel('服务来源')).toHaveValue('none');await expect(wizard.getByLabel('推理服务地址',{exact:true})).toHaveCount(0);await wizard.getByRole('button',{name:'下一步',exact:true}).click();await wizard.getByLabel('客户端资源池',{exact:true}).selectOption('pool');await wizard.getByRole('button',{name:'检查并确认',exact:true}).click();await wizard.getByRole('button',{name:'提交评测批次',exact:true}).click();await expect.poll(()=>submitted?.client.service_mode).toBe('none');expect(submitted.cases[0].episodes).toBe(1);expect(submitted.client.endpoints).toBeUndefined();
});
