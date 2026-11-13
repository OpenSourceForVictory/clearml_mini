import {test,expect} from '@playwright/test';
test('node Agent separates runtime health and desired configuration',async({page})=>{
 let agent:any={status:'online',version:'1.0.0',revision:0,applied_revision:0,config_status:'applied',desired:{heartbeat_seconds:5,sample_seconds:15,programs:'running'},programs:[{name:'mini-eval-worker@gpu0.service',state:'active'}],events:[{sequence:1,code:'started'}]};let update:any;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
 if(p==='auth/me')data={username:'admin',role:'admin'};
 if(p==='admin/nodes')data=[{id:'n',node_name:'host',roles:['client'],enabled:true,status:'online',management_revision:0,max_concurrent:1,cpu_capacity:4,ram_gb_capacity:8,gpu_count:1,held_executions:0,revoked_at:null}];
 if(p==='admin/nodes/n/agent'){if(route.request().method()==='PUT'){update=route.request().postDataJSON();agent={...agent,revision:agent.revision+1,desired:update}}data=agent}
 if(p.endsWith('/storage')||p.endsWith('/runtime'))data={revision:0,config:null,slots:[],all_applied:false};
 await route.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('button',{name:'查看配置'}).click();
 const panel=page.getByRole('region',{name:'节点 Agent',exact:true});
 await expect(panel.getByText(/版本 1.0.0/)).toBeVisible();
 await panel.getByLabel('主机采样周期（秒）').fill('20');await panel.getByRole('button',{name:'保存 Agent 配置'}).click();
 await expect.poll(()=>update?.sample_seconds).toBe(20);
 await panel.getByRole('button',{name:'排空并停止 Worker'}).click();await expect.poll(()=>update?.programs).toBe('stopped');
 await expect(panel.getByRole('button',{name:'恢复 Worker 运行'})).toBeDisabled();
 await panel.getByText('近期 Agent 事件',{exact:true}).click();await expect(panel.getByText('#1 Agent 启动')).toBeVisible();
});
test('legacy machine explicitly reports Agent not installed',async({page})=>{
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
 if(p==='auth/me')data={username:'admin',role:'admin'};
 if(p==='admin/nodes')data=[{id:'n',node_name:'legacy',roles:['client'],enabled:true,status:'online',management_revision:0,max_concurrent:1,cpu_capacity:4,ram_gb_capacity:8,gpu_count:1,held_executions:0}];
 if(p==='admin/nodes/n/agent')data={status:'not_installed',revision:0,desired:{heartbeat_seconds:5,sample_seconds:15,programs:'running'}};
 if(p.endsWith('/storage')||p.endsWith('/runtime'))data={revision:0,config:null,slots:[],all_applied:false};
 await route.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('button',{name:'查看配置'}).click();
 const panel=page.getByRole('region',{name:'节点 Agent',exact:true});await expect(panel.getByText(/此节点尚未安装 Agent/)).toBeVisible();await expect(panel.getByRole('button',{name:'排空并停止 Worker'})).toHaveCount(0);
});
