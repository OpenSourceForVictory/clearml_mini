import {test,expect,type Page} from '@playwright/test';
async function setup(page:Page){
 let reject=false;const deleted:string[]=[];
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'user',role:'user'};
  if(path==='batches')data=[{id:'done',title:'完成批次',status:'SUCCEEDED',total:3,finished:3,succeeded:3,total_episodes:30,max_concurrent:2,created_at:'2026-11-16T01:00:00Z',cases:[{episode_reporting:true,progress:{completed:12}}]},{id:'active',title:'运行批次'+'long'.repeat(100),status:'RUNNING',total:3,finished:1,succeeded:1,total_episodes:30,cases:[]}].filter(b=>!deleted.includes(b.id));
  if(path==='batches/done'&&route.request().method()==='DELETE'){if(reject){await route.fulfill({status:409,json:{detail:'batch services must be stopped and cleaned before deletion'}});return;}deleted.push('done');data={deleted:true};}
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/results');await expect(page.getByRole('row',{name:'批次 完成批次',exact:true})).toBeVisible();
 return {deleted,reject:()=>{reject=true;},allow:()=>{reject=false;}};
}
test('scoped filters, reported progress, responsive long titles and active deletion disabled',async({page})=>{
 await setup(page);
 const done=page.getByRole('row',{name:'批次 完成批次',exact:true});
 await expect(done).toContainText('已上报 12 / 30');
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
