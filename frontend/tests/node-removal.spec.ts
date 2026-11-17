import {test,expect} from '@playwright/test';
test('SSH cleanup and deletion remain separate reviewed actions',async({page})=>{
 let deleted=false,operation:any=null,checked:any;
 const node={id:'node',node_name:'old-node',roles:['client'],enabled:false,status:'unmanaged',revoked_at:'2026-01-01',management_revision:2,gpu_count:1,held_executions:0};
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(p==='auth/me')data={username:'admin',role:'admin'};
  if(p==='admin/nodes')data=deleted?[]:[node];
  if(p==='admin/ssh-onboarding')data={available:true,operations:operation?[operation]:[]};
  if(p==='admin/nodes/node/cleanup/check'){
   checked=route.request().postDataJSON();operation={id:'clean',kind:'cleanup',state:'NEEDS_HOST_KEY',stage:'HOST_KEY',form:{node_id:'node',node_name:'old-node',host:'test'},host_key:{fingerprint:'SHA256:'+'A'.repeat(43)}};data=operation;
  }
  if(p==='admin/ssh-onboarding/clean/trust'){
   operation={...operation,state:'READY',cleanup_plan:{paths:['/opt/mini-eval-updater'],services:['mini-eval-updater.service'],issues:[],backup_root:'/root/mini-eval-node-backups'}};data=operation;
  }
  if(p==='admin/ssh-onboarding/clean/confirm'){
   operation={...operation,state:'SUCCEEDED',cleanup_result:{state:'SUCCEEDED',backup:'/root/mini-eval-node-backups/receipt'}};data=operation;
  }
  if(p==='admin/nodes/node'&&route.request().method()==='DELETE'){expect(route.request().postDataJSON()).toEqual({expected_revision:2});deleted=true;data={deleted:true};}
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('button',{name:'查看配置',exact:true}).click();
 await page.locator('summary').filter({hasText:'清除节点残留'}).click();
 const cleanup=page.getByRole('region',{name:'清除节点残留',exact:true});
 await expect(cleanup.getByLabel('SSH 用户名')).toHaveValue('root');await expect(cleanup.getByLabel('节点访问平台的 API 地址')).toHaveCount(0);
 await cleanup.getByLabel('SSH 主机地址').fill('192.0.2.1');await cleanup.getByLabel('SSH 密码',{exact:true}).fill('secret');
 await cleanup.getByRole('button',{name:'检查连接与环境',exact:true}).click();await expect.poll(()=>checked?.node_id).toBe('node');
 await expect(cleanup.getByLabel('SSH 密码',{exact:true})).toHaveValue('');await cleanup.getByRole('button',{name:'确认此主机指纹并检查环境'}).click();
 await expect(cleanup.getByText('/opt/mini-eval-updater',{exact:true})).toBeVisible();
 page.once('dialog',dialog=>dialog.accept());await cleanup.getByRole('button',{name:'确认计划并清除残留'}).click();
 await expect(cleanup.getByText('恢复清单和备份：/root/mini-eval-node-backups/receipt/manifest.json')).toBeVisible();expect(deleted).toBe(false);
 page.once('dialog',dialog=>dialog.dismiss());await page.getByRole('button',{name:'删除机器',exact:true}).click();expect(deleted).toBe(false);
 page.once('dialog',dialog=>dialog.accept());await page.getByRole('button',{name:'删除机器',exact:true}).click();await expect.poll(()=>deleted).toBe(true);
 await expect(page.getByRole('button',{name:'查看配置',exact:true})).toHaveCount(0);
});
