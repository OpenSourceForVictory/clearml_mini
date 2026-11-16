import {test,expect} from '@playwright/test';
test('SSH wizard checks identity then environment before installing and shows recovery',async({page})=>{
 let operation:any=null;let check:any;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
 if(p==='auth/me')data={username:'admin',role:'admin'};
 if(p==='admin/ssh-onboarding')data={available:true,operations:operation?[operation]:[]};
 if(p==='admin/ssh-onboarding/check'){check=route.request().postDataJSON();operation={id:'op',state:'NEEDS_HOST_KEY',stage:'HOST_KEY',form:{node_name:'new-node',host:'host'},host_key:{fingerprint:'SHA256:'+'A'.repeat(43),algorithm:'ssh-ed25519'}};data=operation;}
 if(p==='admin/ssh-onboarding/op/trust'){expect(route.request().postDataJSON().fingerprint).toBe('SHA256:'+'A'.repeat(43));operation={...operation,state:'READY',stage:'ENVIRONMENT',plan:{business_user:'mini-eval',business_uid:29000,business_gid:29000,accelerator_type:'npu',python_version:'3.11',home:'/home/test',free_disk_gb:100,gpus:[{index:0,name:'RTX'}],selected_gpus:[0],existing:false,services:[],issues:[]}};data=operation;}
 if(p==='admin/ssh-onboarding/op/confirm'){operation={...operation,state:'VERIFYING',stage:'RECONNECT',reconnect:{slots:[{id:'s',gpu_index:0,ready:true}],updater_ready:false}};data=operation;}
 await route.fulfill({json:data});});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('button',{name:'注册新机器',exact:true}).click();
 await page.getByLabel('SSH 机器名称').fill('new-node');await page.getByLabel('SSH 主机地址').fill('192.168.8.157');await expect(page.getByLabel('SSH 用户名')).toHaveValue('root');await page.getByLabel('加速设备类型').selectOption('npu');await page.getByLabel('SSH 密码',{exact:true}).fill('private-password');await page.getByLabel('节点访问平台的 API 地址').fill('http://192.168.8.151:18000');
 await page.getByRole('button',{name:'检查连接与环境',exact:true}).click();await expect.poll(()=>check?.password).toBe('private-password');expect(check.username).toBe('root');expect(check.accelerator_type).toBe('npu');expect(check.business_uid).toBe(29000);expect(check.business_gid).toBe(29000);await expect(page.getByLabel('SSH 密码',{exact:true})).toHaveValue('');
 await expect(page.getByRole('button',{name:'确认计划并自动纳管'})).toHaveCount(0);
 await page.getByRole('button',{name:'确认此主机指纹并检查环境'}).click();await expect(page.getByText('检查通过，等待安装',{exact:true})).toBeVisible();
 await expect(page.getByText('管理身份：root · 宿主机业务：mini-eval · UID/GID 29000/29000')).toBeVisible();await page.getByRole('button',{name:'确认计划并自动纳管'}).click();await expect(page.getByText('升级执行器：等待回连',{exact:true})).toBeVisible();
 await page.reload();await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('button',{name:'注册新机器',exact:true}).click();await expect(page.getByText('等待组件回连',{exact:true})).toBeVisible();
 operation={...operation,state:'SUCCEEDED',stage:'COMPLETE'};await expect(page.getByText('纳管完成。可在资源池管理中分配 GPU。',{exact:true})).toBeVisible({timeout:7000});
});
