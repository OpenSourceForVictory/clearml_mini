import {test,expect} from '@playwright/test';
test('log budgets retain drafts and distinguish saved intent from per-card acknowledgement',async({page})=>{
 let failSave=false;
 let value:any={revision:0,policy:{segment_bytes:4*1024**2,scope_bytes:16*1024**2,execution_bytes:256*1024**2,node_bytes:null},slots:[{id:'a',accelerator_type:'npu',device_index:0,supported:true},{id:'b',accelerator_type:'npu',device_index:1,supported:false}]};
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'admin',username:'admin',role:'admin'};
  if(path==='admin/nodes')data=[{id:'node',node_name:'host',enabled:true,max_concurrent:1}];
  if(path.endsWith('/storage')||path.endsWith('/runtime'))data={revision:0,config:null,slots:[]};
  if(path.endsWith('/execution-data'))data={items:[],slots:[],total:0,policy:{revision:0,enabled:false,days:30}};
  if(path.endsWith('/log-policy')){
   if(route.request().method()==='PUT'){
    if(failSave){await route.fulfill({status:503,json:{detail:'temporarily unavailable'}});return;}
    const b=route.request().postDataJSON();expect(b.expected_revision).toBe(value.revision);expect(b.policy.node_bytes).toBeNull();
    value={...value,revision:value.revision+1,policy:b.policy};
   }data=value;
  }
  await route.fulfill({json:data});
 });
 await page.goto('/');await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('button',{name:'查看配置',exact:true}).click();await page.getByRole('tab',{name:'数据保留',exact:true}).click();await page.getByText('host 执行数据',{exact:true}).click();
 const panel=page.getByRole('group',{name:'独立日志预算'});
 await expect(panel).toContainText('需要升级 Worker');
 await page.getByLabel('单份日志（MiB）').fill('2');await expect(page.getByRole('button',{name:'保存日志预算'})).toBeDisabled();
 await page.getByLabel('单份日志（MiB）').fill('32');await page.getByRole('button',{name:'保存日志预算'}).click();
 await expect(panel).toContainText('配置已保存');await expect(panel).not.toContainText('草稿已保留');await expect(panel).toContainText('等待同步');
 value={...value,slots:[{...value.slots[0],report:{status:'applied',applied_revision:1}}]};
 await expect(panel).toContainText('已生效');
 await page.getByLabel('单份日志（MiB）').fill('64');value={...value,revision:2};
 await expect(panel).toContainText('草稿已保留');await expect(page.getByLabel('单份日志（MiB）')).toHaveValue('64');
 await page.getByRole('button',{name:'重新加载日志预算'}).click();await expect(page.getByLabel('单份日志（MiB）')).toHaveValue('32');
 value={...value,slots:[{...value.slots[0],report:{status:'failed',applied_revision:1,error:'disk full'}}]};
 await expect(panel).toContainText('应用失败：disk full');await expect(panel).toContainText('生效版本 1');
 failSave=true;await page.getByLabel('单份日志（MiB）').fill('64');await page.getByRole('button',{name:'保存日志预算'}).click();
 await expect(panel).toContainText('temporarily unavailable');await expect(page.getByLabel('单份日志（MiB）')).toHaveValue('64');
});
