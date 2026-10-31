import {test,expect,type Page} from '@playwright/test';
import {readFile} from 'node:fs/promises';
async function setup(page:Page){
 let offline=false,delay=false,failDownload=false,requests=0,active=0,maxActive=0;
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const url=new URL(route.request().url()),path=url.pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'user',username:'user',role:'user'};
  if(path==='suites/manage')data={items:[],total:0};
  if(path==='pools')data=[{id:'p',name:'pool'}];
  if(path==='batches')data=Array.from({length:20},(_,i)=>{const id=String((Number(url.searchParams.get('page')||1)-1)*20+i);return {id,title:'批次 '+id,status:'SUCCEEDED',succeeded:1,total:1,finished:1,total_episodes:1,cases:[]};});
  if(path==='batch-comparisons'){
   requests++;active++;maxActive=Math.max(maxActive,active);
   const left=url.searchParams.get('left_id')!,right=url.searchParams.get('right_id')!;
   if(delay)await new Promise(r=>setTimeout(r,700));
   active--;
   if(left==='private'){await route.fulfill({status:404,json:{detail:'batch not found'}});return;}
   if(offline){await route.abort();return;}
   const report=(id:string)=>({generated_at:'2026-10-31T08:00:00Z',batch:{id,title:'批次 '+id},summary:{completed_episodes:1,planned_episodes:2,success_rate:.5,partial:true},cases:[]});
   const item={case_id:'demo',status:'RUNNING',episodes:2,completed_episodes:1,success_rate:.5,config:{image:'left:1'}};
   data={left:report(left),right:report(right),comparable:false,delta_success_rate:null,cases:[{case_id:'demo',left:item,right:{...item,config:{image:'right:2'}},reasons:['incomplete'],definition_differences:[],config_differences:['image'],delta_success_rate:null}]};
  }
  if(path.endsWith('/report')){
   expect(route.request().headers()['authorization']).toBe('Bearer test');
   if(failDownload){await route.fulfill({status:500,json:{detail:'failed'}});return;}
   if(url.searchParams.get('format')==='csv'){await route.fulfill({contentType:'text/csv',body:'\ufeffcase_id,success_rate\r\ndemo,0.5\r\n'});return;}
   data={schema_version:1,batch:{id:path.split('/')[1]},cases:[{case_id:'demo'}]};
  }
  await route.fulfill({json:data});
 });
 return {offline:()=>{offline=true;},delay:()=>{delay=true;},fail:()=>{failDownload=true;},requests:()=>requests,maxActive:()=>maxActive};
}
test('cross-page selection limit, back preserves draft/page and direct refresh',async({page})=>{
 await setup(page);await page.goto('/#/cs');
 await page.getByLabel('批次名称',{exact:true}).fill('保留实验草稿');
 await page.getByLabel('比较选择 批次 0',{exact:true}).check();await page.getByRole('button',{name:'下一页',exact:true}).click();
 await page.getByLabel('比较选择 批次 20',{exact:true}).check();await expect(page.getByLabel('比较选择 批次 21',{exact:true})).toBeDisabled();
 await page.getByRole('button',{name:'比较所选批次'}).click();await expect(page).toHaveURL(/compare\/0\/20/);
 await expect(page.getByText('尚未完整成功执行',{exact:true})).toBeVisible();await expect(page.getByText('image：left:1 → right:2')).toBeVisible();
 await page.getByRole('button',{name:'返回批次列表'}).click();await expect(page.getByLabel('批次名称',{exact:true})).toHaveValue('保留实验草稿');await expect(page.getByText('第 2 页',{exact:true})).toBeVisible();
 await page.getByRole('button',{name:'比较所选批次'}).click();await page.reload();await expect(page.getByText('快照时间：2026-10-31T08:00:00Z')).toBeVisible();
});
test('authenticated JSON CSV downloads and failed download creates no file',async({page})=>{
 const ctl=await setup(page);await page.goto('/#/cs/compare/a/b');
 for(const format of ['JSON','CSV']){
  const ready=page.waitForEvent('download');await page.getByRole('button',{name:`下载 ${format} 报告`}).first().click();const dl=await ready;
  expect(dl.suggestedFilename()).toBe('batch-a.'+format.toLowerCase());
  const content=await readFile((await dl.path())!,'utf8');expect(content).toContain('demo');
  if(format==='JSON')expect(JSON.parse(content).schema_version).toBe(1);else expect(content.charCodeAt(0)).toBe(0xfeff);
 }
 ctl.fail();let downloads=0;page.on('download',()=>downloads++);await page.getByRole('button',{name:'下载 JSON 报告'}).first().click();await expect(page.getByRole('alert')).toContainText('报告下载失败');expect(downloads).toBe(0);
});
test('manual refresh preserves snapshot on error, no overlapping refresh',async({page})=>{
 const ctl=await setup(page);await page.goto('/#/cs/compare/a/b');await expect(page.getByText('快照时间：2026-10-31T08:00:00Z')).toBeVisible();
 await expect(page.getByText('比较快照 · 手动刷新',{exact:true})).toBeVisible();const n=ctl.requests();await page.waitForTimeout(2200);expect(ctl.requests()).toBe(n);
 ctl.delay();await page.getByRole('button',{name:'刷新比较快照'}).click();await expect(page.getByRole('button',{name:'正在读取快照…'})).toBeDisabled();await expect(page.getByRole('button',{name:'刷新比较快照'})).toBeEnabled();expect(ctl.maxActive()).toBe(1);
 ctl.offline();await page.getByRole('button',{name:'刷新比较快照'}).click();await expect(page.getByRole('alert')).toContainText('保留上次成功读取的快照');await expect(page.getByText('快照时间：2026-10-31T08:00:00Z')).toBeVisible();
});
test('switching comparison rejects stale response and private batch clears data',async({page})=>{
 const ctl=await setup(page);ctl.delay();const requested=page.waitForRequest(r=>r.url().includes('left_id=a'));await page.goto('/#/cs/compare/a/b');await requested;
 await page.evaluate(()=>{location.hash='/cs/compare/c/d';});await expect(page.getByRole('heading',{name:'左侧：批次 c'})).toBeVisible();await expect(page.getByRole('heading',{name:'左侧：批次 a'})).toHaveCount(0);
 await page.evaluate(()=>{location.hash='/cs/compare/private/d';});await expect(page.getByRole('alert')).toContainText('batch not found');await expect(page.getByRole('heading',{name:'左侧：批次 c'})).toHaveCount(0);
});
