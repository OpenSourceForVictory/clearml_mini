import {test,expect,type Page} from '@playwright/test';
async function setup(page:Page,outcomes?:any){
 let reject=false;const deleted:string[]=[];
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'user',role:'user'};
  if(path==='batches')data=[{id:'done',title:'完成批次',status:'SUCCEEDED',total:3,finished:3,succeeded:3,total_episodes:30,max_concurrent:2,created_at:'2026-11-16T01:00:00Z',cases:[{episode_reporting:true,progress:{completed:12}}]},{id:'active',title:'运行批次'+'long'.repeat(100),status:'RUNNING',total:3,finished:1,succeeded:1,total_episodes:30,cases:[]}].filter(b=>!deleted.includes(b.id)).map(b=>({...b,outcomes}));
  if(path==='batches/done'&&route.request().method()==='DELETE'){if(reject){await route.fulfill({status:409,json:{detail:'batch services must be stopped and cleaned before deletion'}});return;}deleted.push('done');data={deleted:true};}
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/results');await expect(page.getByRole('row',{name:'批次 完成批次',exact:true})).toBeVisible();
 return {deleted,reject:()=>{reject=true;},allow:()=>{reject=false;}};
}
test('scoped filters, reported progress, responsive long titles and active deletion disabled',async({page})=>{
 await setup(page);
 const done=page.getByRole('row',{name:'批次 完成批次',exact:true});
 await expect(done).toContainText('回合结果待上报');
 await expect(page.getByRole('row',{name:/^批次 /}).nth(1).getByRole('button',{name:'删除批次'})).toBeDisabled();
 await page.getByLabel('当前页状态').selectOption('RUNNING');await expect(page.getByRole('row',{name:/^批次 /})).toHaveCount(1);
 await page.getByLabel('搜索当前页').fill('不存在');await expect(page.getByText('当前页没有匹配的批次。')).toBeVisible();
 await page.getByRole('button',{name:'清除筛选'}).click();await expect(page.getByRole('row',{name:/^批次 /})).toHaveCount(2);
 for(const width of [1440,390]){await page.setViewportSize({width,height:1000});expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);const row=done;expect((await row.boundingBox())!.height).toBeLessThan(90);if(width===390)expect(await page.locator('.history-table-scroll').evaluate(e=>e.scrollWidth>e.clientWidth)).toBe(true);}
});
test('delete confirmation cancel, conflict preserves row, retry clears comparison selection',async({page})=>{
 const ctl=await setup(page);const row=page.getByRole('row',{name:'批次 完成批次',exact:true});
 await page.getByLabel('比较选择 完成批次').check();await row.getByRole('button',{name:'删除批次'}).click();
 await expect(page.getByRole('dialog')).toContainText('不释放磁盘空间');await page.getByRole('button',{name:'暂不删除'}).click();expect(ctl.deleted).toHaveLength(0);
 ctl.reject();await row.getByRole('button',{name:'删除批次'}).click();await page.getByRole('button',{name:'确认删除批次'}).click();await expect(page.getByRole('alert')).toContainText('关联服务尚未停止');await expect(row).toBeVisible();
 ctl.allow();await page.getByRole('button',{name:'确认删除批次'}).click();await expect(row).toHaveCount(0);await expect(page.getByRole('dialog')).toHaveCount(0);await expect(page.getByRole('button',{name:'清空比较选择'})).toBeDisabled();expect(ctl.deleted).toEqual(['done']);
});

test('deleting the only record on last page returns to previous page',async({page})=>{
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 let deleted=false;
 await page.route('**/api/v1/**',async route=>{
  const url=new URL(route.request().url()),path=url.pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'user',role:'user'};
  if(path==='batches')data=(url.searchParams.get('page')==='2'?(deleted?[]:['last']):Array.from({length:20},(_,i)=>String(i))).map(id=>({id,title:id,status:'CANCELLED',total:1,finished:1,succeeded:0,total_episodes:1,cases:[]}));
  if(path==='batches/last'&&route.request().method()==='DELETE'){deleted=true;data={deleted:true};}
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/results');await page.getByRole('button',{name:'下一页',exact:true}).click();
 await page.getByRole('button',{name:'删除批次',exact:true}).click();await page.getByRole('button',{name:'确认删除批次'}).click();
 await expect(page.getByText('第 1 页',{exact:true})).toBeVisible();await expect(page.getByRole('row',{name:/^批次 /})).toHaveCount(20);
});

for(const failure of [{status:500,body:'Internal Server Error',contentType:'text/plain'},{status:502,body:'<html>Bad Gateway</html>',contentType:'text/html'},{status:503,body:'',contentType:'text/plain'}]){
 test(`delete HTTP ${failure.status} preserves row and selection and supports retry`,async({page})=>{
  const ctl=await setup(page);
  await page.route('**/api/v1/batches/done',route=>route.fulfill(failure));
  const row=page.getByRole('row',{name:'批次 完成批次',exact:true});
  await page.getByLabel('比较选择 完成批次').check();
  await row.getByRole('button',{name:'删除批次'}).click();
  await page.getByRole('button',{name:'确认删除批次'}).click();
  await expect(page.getByRole('alert')).toContainText(`请求失败 (${failure.status})`);
  await expect(page.getByRole('alert')).not.toContainText('Unexpected token');
  await expect(row).toBeVisible();await expect(page.getByLabel('比较选择 完成批次')).toBeChecked();
  expect(ctl.deleted).toEqual([]);
  await page.unroute('**/api/v1/batches/done');
  await page.getByRole('button',{name:'确认删除批次'}).click();
  await expect(row).toHaveCount(0);expect(ctl.deleted).toEqual(['done']);
 });
}

test('API handles empty success, invalid success and non-JSON unauthorized responses',async({page})=>{
 await setup(page);
 const call=()=>page.evaluate(async()=>{
  const modulePath='/src/api.ts';const {api}=await import(modulePath);
  try{return {value:await api('diagnostic-test')};}catch(e){return {error:String(e)};}
 });
 await page.route('**/api/v1/diagnostic-test',route=>route.fulfill({status:204}));
 expect(await call()).toEqual({value:undefined});
 await page.unroute('**/api/v1/diagnostic-test');
 await page.route('**/api/v1/diagnostic-test',route=>route.fulfill({status:200,body:'<html>proxy</html>'}));
 expect((await call()).error).toContain('响应格式错误 (200)');
 await page.unroute('**/api/v1/diagnostic-test');
 await page.route('**/api/v1/diagnostic-test',route=>route.fulfill({status:401,body:'Unauthorized'}));
 expect((await call()).error).toContain('请求失败 (401)');
 expect(await page.evaluate(()=>sessionStorage.getItem('eval-token'))).toBeNull();
});

test('outcome text stays within separate columns at desktop and narrow widths',async({page})=>{
 const outcomes={execution:{normal:100000,abnormal:10,running:20,unknown:30,not_started:40},protocol:{planned:100000,success:90000,failure:100,invalid:30,errors:10,unreported:9860,not_applicable_tasks:2,valid:90100,success_rate:90000/90100}};
 await setup(page,outcomes);
 for(const width of [1440,1024,390]){
  await page.setViewportSize({width,height:1000});
  const cells=page.getByRole('row',{name:'批次 完成批次',exact:true}).locator('td:nth-child(7),td:nth-child(8),td:nth-child(9)');
  expect(await cells.evaluateAll(elements=>elements.every(cell=>{
   const box=cell.getBoundingClientRect(),walker=document.createTreeWalker(cell,NodeFilter.SHOW_TEXT);
   let node;while((node=walker.nextNode())){if(!node.textContent?.trim())continue;const range=document.createRange();range.selectNodeContents(node);
    for(const rect of range.getClientRects())if(rect.left<box.left-1||rect.right>box.right+1)return false;
   }return true;
  }))).toBe(true);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
 }
});

const baseOutcome={execution:{normal:9,abnormal:1,running:2,unknown:1,not_started:7},protocol:{planned:20,success:8,failure:2,invalid:1,errors:1,unreported:8,not_applicable_tasks:1,valid:10,success_rate:.8}};
test('list displays two weighted rates and coverage without detailed counters',async({page})=>{
 await setup(page,baseOutcome);const row=page.getByRole('row',{name:'批次 完成批次',exact:true});
 await expect(row).toContainText('正常退出率 90.0%（9/10）');await expect(row).toContainText('退出码已知 10/20 次');
 await expect(row).toContainText('评测成功率 80.0%（8/10）');await expect(row).toContainText('有效结果 10/20 回合');await expect(row).toContainText('1 项未接入');
 await expect(row).not.toContainText('正常结束 9 次');await expect(row).not.toContainText('业务失败 2');
 await expect(page.getByRole('row',{name:/^批次 运行批次/})).toContainText('已结束 1/3');
});
for(const kind of ['pending','invalid','command','failed'])test(`zero and missing denominators ${kind}`,async({page})=>{
 const value=JSON.parse(JSON.stringify(baseOutcome));value.execution={normal:0,abnormal:kind==='failed'?2:0,running:0,unknown:kind==='pending'?2:0,not_started:1};
 value.protocol={planned:kind==='command'?0:3,success:0,failure:kind==='failed'?2:0,invalid:kind==='invalid'?3:0,errors:0,unreported:kind==='pending'?3:0,not_applicable_tasks:kind==='command'?1:0,valid:kind==='failed'?2:0,success_rate:kind==='failed'?0:null};
 await setup(page,value);const row=page.getByRole('row',{name:'批次 完成批次',exact:true});
 await expect(row).toContainText(kind==='failed'?'正常退出率 0.0%（0/2）':'正常退出率 暂无数据');
 await expect(row).toContainText(kind==='command'?'未接入（普通命令）':kind==='invalid'?'暂无有效结果':kind==='failed'?'评测成功率 0.0%（0/2）':'评测成功率 待上报');
 if(kind!=='failed')await expect(row).not.toContainText('0.0%');
});
