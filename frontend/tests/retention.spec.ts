import {test,expect,type Page} from '@playwright/test';
async function setup(page:Page,role='admin') {
 let confirms=0;
 let inventory:any={items:[{id:'data',generation:'g1',slot_id:'slot',execution_id:'execution',root_path:'/work/runs',entry_name:'execution',logical_bytes:null,allocated_bytes:null,scan_status:'partial',scanned_at:'today',blocked_reason:'scan in progress',last_cleanup_status:null}],total:1,scan_revision:0,policy:{revision:0,enabled:false,days:30},slots:[{id:'slot',online:true,supported:true}]};
 let op:any={id:'preview',status:'preview',source:'manual',expires_at:new Date(Date.now()+600000).toISOString(),items:[{id:'item',data_id:'data',status:'preview',reason:null,snapshot:{entry_name:'execution',allocated_bytes:100},receipt:null}]};
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let body:any=[];
  if(path==='auth/me')body={id:'admin',username:'admin',role};
  else if(path==='admin/nodes')body=[{id:'node',node_name:'host',enabled:true,max_concurrent:1,cpu_capacity:4,ram_gb_capacity:8}];
  else if(path==='admin/nodes/node/storage')body={revision:0,config:null,slots:[]};
  else if(path==='admin/nodes/node/runtime')body={revision:0,config:null,slots:[]};
  else if(path==='admin/nodes/node/execution-data')body=inventory;
  else if(path.endsWith('/operations'))body=[];
  else if(path.endsWith('/confirm')){confirms++;await new Promise(r=>setTimeout(r,150));body={...op,status:'queued'};}
  else if(path.endsWith('/preview'))body=op;
  else if(path.endsWith('/retention')){const data=route.request().postDataJSON();inventory.policy={revision:inventory.policy.revision+1,enabled:data.enabled,days:data.days};body=inventory.policy;}
  await route.fulfill({json:body});
 });
 await page.goto('/');
 if(role==='admin'){await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByText('host 执行数据',{exact:true}).click();}
 return {change:(v:any)=>inventory={...inventory,...v},expire:()=>op={...op,expires_at:new Date(Date.now()-1000).toISOString()},confirms:()=>confirms};
}
test('inventory unknown sizes, drafts and selections survive polls, confirmation is asynchronous',async({page})=>{
 const state=await setup(page);
 await expect(page.getByText('未知 / 未知',{exact:true})).toBeVisible();
 await page.getByLabel('选择 execution',{exact:true}).check();
 await page.getByLabel('保留天数',{exact:true}).fill('45');
 state.change({scan_revision:3,slots:[{id:'slot',online:false,supported:false}]});
 await expect(page.getByText(/扫描请求版本 3/)).toBeVisible();
 await expect(page.getByLabel('保留天数',{exact:true})).toHaveValue('45');
 await expect(page.getByLabel('选择 execution',{exact:true})).toBeChecked();
 await expect(page.getByText(/Worker 离线/)).toBeVisible();
 await expect(page.getByText(/需升级到 v6/)).toBeVisible();
 await page.getByRole('button',{name:'预览所选清理',exact:true}).click();
 await page.getByRole('button',{name:'确认不可逆清理',exact:true}).dblclick();
 await expect(page.getByRole('status')).toHaveText(/尚不代表删除成功/);
 expect(state.confirms()).toBe(1);
 await page.screenshot({path:'test-results/retention.png',fullPage:true});
});
test('policy version conflicts preserve drafts and expired previews disable confirmation',async({page})=>{
 const state=await setup(page);
 await page.getByLabel('保留天数',{exact:true}).fill('60');
 state.change({policy:{revision:1,enabled:false,days:30}});
 await expect(page.getByText(/策略版本已变化/)).toBeVisible();
 await expect(page.getByLabel('保留天数',{exact:true})).toHaveValue('60');
 await page.getByRole('button',{name:'重新加载策略',exact:true}).click();
 await expect(page.getByLabel('保留天数',{exact:true})).toHaveValue('30');
 await page.getByLabel('启用自动清理',{exact:true}).check();
 state.expire();await page.getByRole('button',{name:'预览策略影响',exact:true}).click();
 await expect(page.getByText('策略预览已过期。')).toBeVisible();
 await expect(page.getByRole('button',{name:'确认启用策略',exact:true})).toBeDisabled();
});
test('ordinary users cannot see execution data controls',async({page})=>{
 await setup(page,'user');await expect(page.getByText('host 执行数据',{exact:true})).toHaveCount(0);
});

test('late preview response cannot populate a reopened machine panel',async({page})=>{
 await setup(page);
 let finish:()=>void=()=>{};
 const gate=new Promise<void>(resolve=>finish=resolve);
 await page.route('**/api/v1/admin/nodes/node/execution-data/preview',async route=>{
  await gate;
  await route.fulfill({json:{id:'late',status:'preview',source:'manual',expires_at:new Date(Date.now()+600000).toISOString(),items:[]}});
 });
 await page.getByLabel('选择 execution',{exact:true}).check();
 await page.getByRole('button',{name:'预览所选清理',exact:true}).click();
 await page.getByText('host 执行数据',{exact:true}).click();
 finish();
 await page.getByText('host 执行数据',{exact:true}).click();
 await expect(page.getByRole('region',{name:'清理预览',exact:true})).toHaveCount(0);
 await expect(page.getByLabel('选择 execution',{exact:true})).not.toBeChecked();
});
