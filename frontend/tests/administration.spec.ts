import {test,expect} from '@playwright/test';
test('management navigation and machine roles use a selected detail',async({page})=>{
  await page.setViewportSize({width:1440,height:600});
  let nodes=[{id:'n',node_name:'host',roles:['client','server'],enabled:true,status:'online',management_revision:0,max_concurrent:1,cpu_capacity:4,ram_gb_capacity:8,gpu_count:1,held_executions:0,revoked_at:null}];
  let update:any;
  await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
  await page.route('**/api/v1/**',async route=>{
    const p=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
    if(p==='auth/me')data={username:'admin',role:'admin'};
    if(p==='admin/nodes')data=nodes;
    if(p==='admin/nodes/n'){update=route.request().postDataJSON();nodes=[{...nodes[0],...update,management_revision:1}];data=nodes[0]}
    if(p.endsWith('/storage'))data={revision:0,config:null,slots:[],all_applied:false};
    if(p.endsWith('/runtime'))data={revision:0,config:null,slots:[],all_applied:false};
    if(p==='admin/users')data=[{id:'a',username:'admin',role:'admin',active:true}];
    await route.fulfill({json:data});
  });
  await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();
  await expect(page.getByRole('tab',{name:'运行模板管理'})).toBeVisible();
  await expect(page.getByRole('heading',{name:'host · 配置详情'})).toHaveCount(0);
  await page.getByRole('button',{name:'查看配置'}).click();
  await expect(page.getByRole('heading',{name:'host · 配置详情'})).toBeInViewport();
  await expect(page.getByRole('region',{name:'host · 配置详情'})).toBeFocused();
  await page.getByRole('button',{name:'停用（停止接新任务）'}).click();
  await expect.poll(()=>update).toEqual({enabled:false,expected_revision:0});
  await page.getByRole('tab',{name:'服务端机器'}).click();
  await expect(page.getByText('host',{exact:true})).toBeVisible();
  await expect(page.getByRole('heading',{name:'host · 配置详情'})).toHaveCount(0);
  await page.getByRole('tab',{name:'账号管理'}).click();await page.getByRole('tab',{name:'创建账号',exact:true}).click();await expect(page.getByRole('heading',{name:'创建账号'})).toBeVisible();
});
