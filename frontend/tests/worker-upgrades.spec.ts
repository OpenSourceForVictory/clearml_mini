import {test,expect} from '@playwright/test';
test('version management distinguishes protocol, software and drain upgrade',async({page})=>{
 let submitted:any;let revision=0;let operation:any=null;const release={id:'a'.repeat(64),version:'0.10.0',protocol_version:9,capabilities:['directory_mounts']};
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
 if(p==='auth/me')data={username:'admin',role:'admin'};
 if(p==='admin/nodes')data=[{id:'n',node_name:'test',roles:['client'],enabled:true,status:'online',management_revision:0}];
 if(p.endsWith('/storage')||p.endsWith('/runtime'))data={revision:0,config:null,slots:[],all_applied:false};
 if(p==='admin/worker-releases')data=[release];
 if(p==='admin/nodes/n/upgrades'){
  if(route.request().method()==='POST'){submitted=route.request().postDataJSON();operation={id:'u',status:'DRAINING',release_id:release.id,created_at:'now',error:null};revision++;}
  data={revision,state:operation?.status,updater_online:true,slots:[{id:'s',gpu_index:0,online:true,protocol_version:8,software:{version:'0.9.0',build_digest:null,capabilities:['typed_parameters']}}],history:operation?[operation]:[]};
 }
 if(p==='admin/nodes/n/upgrades/u/cancel'){expect(route.request().postDataJSON()).toEqual({expected_revision:1});operation.status='CANCELLED';revision++;}
 await route.fulfill({json:data})});
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('button',{name:'查看配置',exact:true}).click();await page.getByRole('tab',{name:'版本升级',exact:true}).click();await page.getByText('Worker 版本与升级',{exact:true}).click();
 await expect(page.getByRole('cell').filter({hasText:'0.9.0'})).toBeVisible();await expect(page.getByText('v8',{exact:true})).toBeVisible();
 await page.getByLabel('目标 Worker 发布版本').selectOption(release.id);await page.getByRole('button',{name:'排空并升级此机器'}).click();
 await expect.poll(()=>submitted).toEqual({release_id:release.id,expected_revision:0});await expect(page.getByRole('button',{name:'排空并升级此机器'})).toBeDisabled();
 await page.getByRole('button',{name:'取消升级，恢复接单'}).click();await expect(page.getByRole('button',{name:'排空并升级此机器'})).toBeEnabled();
});
