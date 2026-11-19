import {test,expect,Page} from '@playwright/test';
async function openSuite(page:Page,status='draft',archived=false){
 const suite={id:'s',name:'selection',version:'1',template_id:'t',tags:[],status,archived,revision:0,description:'',source:'',cases:Array.from({length:50},(_,i)=>({case_id:`case-${i}`,task_name:`task-${i}`,episodes:3,seed:0,parameters:{keep:i}})),task_count:50,total_episodes:150,latest_run:null};
 await page.addInitScript(()=>sessionStorage.setItem('eval-token','test'));
 await page.route('**/api/v1/**',async route=>{
  const path=new URL(route.request().url()).pathname.replace('/api/v1/','');let data:any=[];
  if(path==='auth/me')data={id:'u',username:'user',role:'user'};
  if(path==='client-templates')data=[{id:'t',name:'client',version:'1'}];
  if(path==='suites/manage')data={items:[suite],total:1};
  if(path==='suites/s')data=suite;
  await route.fulfill({json:data});
 });
 await page.goto('/#/cs/results');await page.getByRole('tab',{name:'评测任务集合管理'}).click();
 await page.getByRole('row',{name:'集合 selection / 1',exact:true}).getByRole('button').click();
}
test('50 task selection ignores search and bulk edits include hidden rows',async({page})=>{
 await openSuite(page);
 const summary=page.getByRole('status').filter({hasText:'已选中'});
 await page.getByLabel('搜索清单任务').fill('task-49');
 await page.getByRole('button',{name:'选择全部任务',exact:true}).click();
 await expect(summary).toContainText('已选中 50 / 50 项，其中 49 项被搜索隐藏');
 await page.getByLabel('批量回合数').fill('10');await page.getByLabel('批量种子').fill('42');
 await page.getByLabel('批量参数',{exact:true}).fill('{"new":true}');
 await page.getByRole('button',{name:'应用到选中 50 项'}).click();
 await page.getByLabel('搜索清单任务').fill('');
 for(let i=0;i<50;i++){
  await expect(page.getByLabel('任务回合 '+i,{exact:true})).toHaveValue('10');
  await expect(page.getByLabel('任务种子 '+i,{exact:true})).toHaveValue('42');
  await expect(page.getByLabel('任务参数 '+i,{exact:true})).toHaveValue(JSON.stringify({keep:i,new:true}));
 }
 await page.getByLabel('搜索清单任务').fill('not-found');
 await page.getByRole('button',{name:'清空任务选择'}).click();
 await page.getByRole('button',{name:'选择全部任务',exact:true}).click();
 await expect(summary).toContainText('其中 50 项被搜索隐藏');
 await page.getByRole('button',{name:'选中当前显示任务',exact:true}).click();
 await expect(page.getByRole('button',{name:'应用到选中 0 项'})).toBeDisabled();
 await page.getByRole('button',{name:'选择全部任务',exact:true}).click();
 await page.getByLabel('搜索清单任务').fill('task-49');
 await page.getByRole('button',{name:'选中当前显示任务',exact:true}).click();
 await expect(summary).toContainText('已选中 1 / 50 项，其中 0 项被搜索隐藏');
 await page.getByRole('button',{name:'仅保留选中任务'}).click();
 await expect(summary).toContainText('已选中 0 / 1 项');
 await page.getByRole('button',{name:'选择全部任务',exact:true}).click();
 await page.getByRole('button',{name:'清空任务选择'}).click();
 await expect(page.getByRole('button',{name:'移除选中任务'})).toBeDisabled();
});
for(const [status,archived] of [['published',false],['draft',true]] as const){
 test(`selection readonly ${status} archived=${archived}`,async({page})=>{
  await openSuite(page,status,archived);await page.getByLabel('搜索清单任务').fill('task-49');
  for(const name of ['选择全部任务','选中当前显示任务','清空任务选择','移除选中任务'])await expect(page.getByRole('button',{name,exact:true})).toBeDisabled();
  await expect(page.getByLabel('选择任务 task-49',{exact:true})).toBeDisabled();
 });
}
