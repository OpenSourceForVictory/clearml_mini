import {test,expect} from '@playwright/test';
test('SSH re-enrollment preserves modes by default and sends an explicit override',async({page})=>{
 let submitted:any;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(p==='auth/me')data={username:'admin',role:'admin'};
  if(p==='admin/nodes')data=[{id:'n',node_name:'original-node',roles:['client'],enabled:false,status:'unmanaged',revoked_at:'old',management_revision:1,connection:{}}];
  if(p==='admin/nodes/n/enrollment')data={state:null,revision:1,ready:false,slots:[]};
  if(p==='admin/ssh-onboarding')data={available:true,operations:[]};
  if(p==='admin/ssh-onboarding/check'){submitted=route.request().postDataJSON();data={id:'check',state:'CHECKING',stage:'SSH',form:{node_id:'n',node_name:'original-node',host:submitted.host,mode:submitted.mode}};}
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('button',{name:'查看配置',exact:true}).click();
 const wizard=page.getByRole('region',{name:'重新纳管'}).getByRole('region',{name:'SSH 自动纳管'});
 await expect(wizard.getByLabel('执行环境')).toHaveValue('preserve');
 await wizard.getByLabel('执行环境').selectOption('both');
 await wizard.getByLabel('SSH 主机地址').fill('127.0.0.1');
 await wizard.getByLabel('SSH 用户名').fill('root');
 await wizard.getByLabel('SSH 密码',{exact:true}).fill('test-password');
 await wizard.getByLabel('节点访问平台的 API 地址').fill('http://127.0.0.1:18000');
 await wizard.getByRole('button',{name:'检查连接与环境'}).click();
 await expect.poll(()=>submitted?.mode).toBe('both');
 expect(submitted.node_id).toBe('n');
});
test('unmanaged machine re-enrolls in place and requires all components',async({page})=>{
 let started=false,ready=false,complete=false;let revision=1;let copies=0;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
 if(p==='auth/me')data={username:'admin',role:'admin'};
 if(p==='admin/nodes')data=[{id:'n',node_name:'original-node',roles:['client'],enabled:complete,status:complete?'online':started?'enrolling':'unmanaged',revoked_at:started?null:'old',management_revision:revision,connection:{enrollment:{state:complete?'COMPLETE':started?'INSTALLING':null}}}];
 if(p.endsWith('/storage')||p.endsWith('/runtime'))data={revision:0,config:null,slots:[]};
 if(p.endsWith('/upgrades'))data={slots:[],history:[],updater_online:false};
 if(p==='admin/nodes/n/reenroll'){expect(route.request().postDataJSON()).toEqual({expected_revision:1});started=true;revision=2;data={node_name:'original-node',token:'one-time',release_id:'a'.repeat(64)};}
 if(p==='admin/nodes/n/enrollment')data={state:started?'INSTALLING':null,revision,ready,updater_ready:ready,slots:[{id:'s0',gpu_index:0,ready:true},{id:'s1',gpu_index:1,ready}]};
 if(p==='admin/node-installation-bundle'){copies++;data={filename:'mini-eval-node.zip',archive:btoa('test'),release_id:'a'.repeat(64),sha256:'b'.repeat(64)}}
 if(p==='admin/nodes/n/enrollment/complete'){expect(ready).toBe(true);expect(route.request().postDataJSON()).toEqual({expected_revision:2});complete=true;revision=3;data={state:'COMPLETE',slots:[]};}
 await route.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('button',{name:'查看配置',exact:true}).click();
 await page.getByText('手动重新纳管',{exact:true}).click();
 await page.getByRole('button',{name:'开始重新纳管',exact:true}).click();
 await expect(page.getByRole('button',{name:'完成纳管并启用'})).toBeDisabled();
 await expect(page.getByText('GPU 1：等待目标版本回连',{exact:true})).toBeVisible();
 const dl=page.waitForEvent('download');await page.getByRole('button',{name:'下载完整节点安装包'}).click();expect((await dl).suggestedFilename()).toBe('mini-eval-node.zip');expect(copies).toBe(1);
 await expect(page.getByRole('button',{name:'完成纳管并启用'})).toBeDisabled();
 ready=true;await expect(page.getByRole('button',{name:'完成纳管并启用'})).toBeEnabled({timeout:8000});
 await page.getByRole('button',{name:'完成纳管并启用'}).click();await expect(page.getByRole('region',{name:'重新纳管',exact:true})).toHaveCount(0);
 expect(complete).toBe(true);
});
