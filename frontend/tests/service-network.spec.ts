import {test,expect} from '@playwright/test';
test('forwarded service distinguishes reachable and local addresses and restores default',async({page})=>{
 let network:any={revision:0,config:{address:'192.168.8.151',port_start:19400,port_end:19415}},saved:any;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
 if(p==='auth/me')data={username:'admin',role:'admin'};
 if(p==='admin/nodes')data=[{id:'n',node_name:'ascend-server',roles:['server'],enabled:true,status:'online',management_revision:0,max_concurrent:1,cpu_capacity:16,ram_gb_capacity:64,gpu_count:2,held_executions:0}];
 if(p==='admin/nodes/n/service-network'){if(route.request().method()==='PUT'){saved=route.request().postDataJSON();network={revision:network.revision+1,config:saved.config}}data=network;}
 if(p==='admin/nodes/n/agent')data={status:'not_installed',revision:0,desired:{heartbeat_seconds:5,sample_seconds:15,programs:'running'}};
 if(p.endsWith('/storage')||p.endsWith('/runtime'))data={revision:0,config:null,slots:[],all_applied:false};
 await route.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'服务端机器'}).click();await page.getByRole('button',{name:'查看配置'}).click();await page.getByRole('tab',{name:'服务网络',exact:true}).click();
 const panel=page.getByRole('region',{name:'服务自动分配网络'});
 await expect(panel.getByLabel('服务可达 IPv4')).toHaveValue('192.168.8.151');
 await panel.getByLabel('节点本地监听 IPv4（可选）').fill('127.0.0.1');await panel.getByRole('button',{name:'保存服务网络'}).click();
 await expect.poll(()=>saved?.config.bind_address).toBe('127.0.0.1');expect(saved.config.address).toBe('192.168.8.151');
 await panel.getByRole('button',{name:'重新读取网络配置'}).click();await expect(panel.getByLabel('节点本地监听 IPv4（可选）')).toHaveValue('127.0.0.1');
 await panel.getByLabel('节点本地监听 IPv4（可选）').fill('');await panel.getByRole('button',{name:'保存服务网络'}).click();
 await expect.poll(()=>saved?.config.bind_address).toBeNull();expect(saved.expected_revision).toBe(1);
});
