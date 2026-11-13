import {test,expect} from '@playwright/test';
for(const mobile of [false,true]) test(`machine filters and management layout ${mobile?'mobile':'desktop'}`,async({page})=>{
  if(mobile) await page.setViewportSize({width:390,height:844});
  await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
  let fail=true;
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1/','');
    if(path==='admin/nodes'&&fail) {fail=false;await route.fulfill({status:503,json:{detail:'Temporary unavailable'}});return;}
    let data:any=[];
    if(path==='auth/me')data={username:'admin',role:'admin'};
    if(path==='admin/nodes')data=[['alpha-4090','online'],['beta-3080','offline']].map(([node_name,status],i)=>({id:String(i),node_name,status,roles:['client'],enabled:true,gpu_count:2,held_executions:i,revoked_at:null}));
    await route.fulfill({json:data});
  });
  await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();
  await expect(page.getByRole('button',{name:'重试加载'})).toBeVisible();
  await expect(page.getByText('暂无此类型机器',{exact:false})).toHaveCount(0);
  await page.getByRole('button',{name:'重试加载'}).click();
  await expect(page.getByText('显示 2 / 2 台机器')).toBeVisible();
  await page.getByLabel('搜索机器').fill('ALPHA');
  await expect(page.getByText('显示 1 / 2 台机器')).toBeVisible();
  await expect(page.getByRole('region',{name:'机器列表'}).getByText('beta-3080')).toHaveCount(0);
  await page.getByLabel('机器状态',{exact:true}).selectOption('offline');
  await expect(page.getByText('没有匹配的机器，请调整名称或状态筛选。')).toBeVisible();
  await expect(page.getByLabel('当前类型机器概览')).toContainText('已注册机器2');
  await page.getByRole('button',{name:'清除筛选'}).click();
  await expect(page.getByText('显示 2 / 2 台机器')).toBeVisible();
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:`test-results/admin-${mobile?'mobile':'desktop'}.png`,fullPage:true});
  await page.getByRole('tab',{name:'服务端机器'}).click();
  await expect(page.getByText('暂无此类型机器，可注册新机器或在已有机器详情中增加角色。')).toBeVisible();
});
