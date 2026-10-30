import {test, expect, type Page} from '@playwright/test';

async function setup(page: Page, role='admin') {
  let value:any={revision:1,config:{workspace_root:'/srv/work',assets_root:'/srv/assets',cache_root:'/srv/cache',min_free_disk_gb:2},all_applied:false,slots:[
    {slot_id:'s1',gpu_index:0,status:'applied',applied_revision:1,reported_at:'2026-10-30',report:{effective:{workspace_root:'/srv/work/slots/s1',assets_root:'/srv/assets',cache_root:'/srv/cache',min_free_disk_gb:2},capacity:{workspace:1024}}},
    {slot_id:'s2',gpu_index:1,status:'waiting',applied_revision:0,report:{}}]};
  let submitted:any;
  await page.addInitScript(()=>sessionStorage.setItem('eval-token','ui-test'));
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1/','');
    let body:any=[];
    if(path==='auth/me')body={id:'user',username:'tester',role};
    else if(path==='admin/nodes')body=[{id:'node',node_name:'host',enabled:true,max_concurrent:2,cpu_capacity:8,ram_gb_capacity:32}];
    else if(path==='admin/health')body={runner_image:'runner:1'};
    else if(path==='admin/nodes/node/storage') {
      if(route.request().method()==='PUT') {
        submitted=route.request().postDataJSON();
        if(submitted.expected_revision!==value.revision) {await route.fulfill({status:409,json:{detail:'storage configuration changed; reload before saving'}});return;}
        value={...value,revision:value.revision+1,config:submitted.config};
      }
      body=value;
    }
    await route.fulfill({json:body});
  });
  await page.goto('/');
  return {change:(update:any)=>{value={...value,...update};},submitted:()=>submitted};
}

test('storage drafts survive polling, conflict is visible, restore is versioned',async({page})=>{
  const state=await setup(page);
  await page.getByRole('button',{name:'管理设置',exact:true}).click();
  await page.getByText('host 存储配置',{exact:true}).click();
  await expect(page.getByText(/GPU 1 · 等待任务结束/)).toBeVisible();
  await expect(page.getByText(/工作盘空间不足/)).toBeVisible();
  await page.getByLabel('host 工作基目录',{exact:true}).fill('/new/work');
  state.change({revision:2});
  await expect(page.getByText(/期望版本 2/)).toBeVisible({timeout:10000});
  await expect(page.getByLabel('host 工作基目录',{exact:true})).toHaveValue('/new/work');
  await page.getByRole('button',{name:'保存存储配置 / 重试校验',exact:true}).click();
  expect(state.submitted().expected_revision).toBe(1);
  await expect(page.getByRole('alert').filter({hasText:'storage configuration changed'})).toBeVisible();
  await page.getByRole('button',{name:'重新载入最新配置（放弃草稿）',exact:true}).click();
  await page.getByRole('button',{name:'恢复本机配置',exact:true}).click();
  await expect(page.getByText('版本 3 已保存，等待 Worker 校验确认。')).toBeVisible();
  expect(state.submitted()).toEqual({expected_revision:2,config:null});
  state.change({slots:[{slot_id:'s1',gpu_index:0,status:'failed',applied_revision:1,report:{error:'shared directory missing'}},{slot_id:'s2',gpu_index:1,status:'offline',applied_revision:0,report:{}}]});
  await expect(page.getByText('shared directory missing')).toBeVisible({timeout:10000});
  await expect(page.getByText(/GPU 1 · 离线/)).toBeVisible();
  await page.screenshot({path:'test-results/worker-storage.png',fullPage:true});
});

test('ordinary users do not get machine storage controls',async({page})=>{
  await setup(page,'user');
  await expect(page.getByRole('button',{name:'管理设置',exact:true})).toHaveCount(0);
  await expect(page.getByText('host 存储配置')).toHaveCount(0);
});
