import {test, expect, type Page} from '@playwright/test';

async function openWorkbench(page:Page, role='admin') {
  const suites:any[]=[];
  const batches:any[]=[];
  const template={id:'client-1',name:'RoboTwin',version:'1',enabled:true,image:'client:1',protocols:['ws'],command:['python','-m','runner.sdk']};
  let submitted:any;
  await page.addInitScript(()=>sessionStorage.setItem('eval-token','ui-test'));
  await page.route('**/api/v1/**',async route=>{
    const path=new URL(route.request().url()).pathname.replace('/api/v1/','');
    let body:any=[];
    if(path==='auth/me') body={id:'user-1',username:'tester',role};
    else if(path==='client-templates') body=[template];
    else if(path==='suites/manage') body={items:[],total:0};
    else if(path==='suites/preview') {const doc=JSON.parse(route.request().postDataJSON().content);body={valid:true,errors:[],document:doc,task_count:doc.cases.length,total_episodes:3};}
    else if(path==='pools') body=[{id:'pool-1',name:'gpu-pool'}];
    else if(path==='suites') {
      if(route.request().method()==='POST') {body={id:'suite-1',revision:0,archived:false,...route.request().postDataJSON()};suites.push(body);}
      else body=suites;
    } else if(path==='orchestrations/preflight') {const b=route.request().postDataJSON();body={checked_at:new Date().toISOString(),can_submit:true,status:'ready',cases:suites.find(s=>s.id===b.suite_id)?.cases||b.cases,nodes:[],reasons:[]};
    } else if((path==='batches'||path==='orchestrations')) {
      if(route.request().method()==='POST') {
        submitted=route.request().postDataJSON();
        body={id:'batch-1',title:submitted.title,status:'PENDING',total:1,succeeded:0,finished:0,total_episodes:3,cases:[]};batches.push(body);
      } else body=batches;
    }
    await route.fulfill({json:body});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'业务评测向导',exact:true}).click();
  return ()=>submitted;
}

test('collection management publishes and hands off to the business wizard',async({page})=>{
 const submitted=await openWorkbench(page);
 await page.getByRole('button',{name:'管理设置',exact:true}).click();await page.getByRole('tab',{name:'评测集合管理',exact:true}).click();
 const registration=page.getByRole('tabpanel',{name:'评测集合管理',exact:true});
 await registration.getByLabel('集合名称',{exact:true}).fill('RoboTwin full');await registration.getByLabel('集合客户端模板',{exact:true}).selectOption('client-1');
 await registration.getByRole('button',{name:'添加任务',exact:true}).click();await registration.getByLabel('任务名称 0',{exact:true}).fill('demo');await registration.getByRole('button',{name:'发布并运行',exact:true}).click();
 const runs=page.getByRole('region',{name:'集合运行向导'});await expect(runs).toBeVisible();await runs.getByLabel('批次名称',{exact:true}).fill('RoboTwin batch');await expect(runs.getByLabel('选择已保存集合')).toHaveValue('suite-1');
 await runs.getByRole('button',{name:'下一步',exact:true}).click();await runs.getByRole('button',{name:'下一步',exact:true}).click();await runs.getByLabel('推理服务地址',{exact:true}).fill('ws://inference:8000');await runs.getByRole('button',{name:'下一步',exact:true}).click();await runs.getByLabel('客户端资源池',{exact:true}).selectOption('pool-1');await runs.getByLabel('最大并发任务数').fill('2');await runs.getByRole('button',{name:'检查并确认',exact:true}).click();await runs.getByRole('button',{name:'提交评测批次',exact:true}).click();
 expect(submitted()).toMatchObject({suite_id:'suite-1',cases:[],max_concurrent:2,client:{template_id:'client-1',endpoints:[{url:'ws://inference:8000',mode:'shared'}]}});
 await page.getByRole('button',{name:'评测运行与结果',exact:true}).click();await expect(page.getByRole('row',{name:'批次 RoboTwin batch',exact:true})).toBeVisible();await expect(runs).toBeHidden();
});
test('member keeps own collection and source registration without admin controls',async({page})=>{
 await openWorkbench(page,'member');await page.getByRole('button',{name:'评测配置',exact:true}).click();await page.getByRole('tab',{name:'客户端接入',exact:true}).click();await page.getByRole('tab',{name:'代码构建',exact:true}).click();
 await expect(page.getByRole('heading',{name:'提交仿真客户端代码'})).toBeVisible();await expect(page.getByRole('button',{name:'管理设置',exact:true})).toHaveCount(0);await expect(page.getByRole('button',{name:'登记客户端模板',exact:true})).toHaveCount(0);
 await page.getByRole('tab',{name:'代码构建',exact:true}).focus();await page.keyboard.press('Home');await expect(page.getByRole('tab',{name:'镜像接入',exact:true})).toBeFocused();
});

test('results collection subtab preserves draft and hands off to wizard for members',async({page})=>{
 const submitted=await openWorkbench(page,'user');
 await page.getByRole('button',{name:'评测运行与结果',exact:true}).click();await page.getByRole('tab',{name:'评测任务集合管理',exact:true}).click();
 const registration=page.getByRole('tabpanel',{name:'评测任务集合管理',exact:true});
 await registration.getByLabel('集合名称',{exact:true}).fill('RoboTwin full');await page.getByRole('tab',{name:'运行记录',exact:true}).click();await page.getByRole('tab',{name:'评测任务集合管理',exact:true}).click();await expect(registration.getByLabel('集合名称',{exact:true})).toHaveValue('RoboTwin full');await registration.getByLabel('集合客户端模板',{exact:true}).selectOption('client-1');
 await registration.getByRole('button',{name:'添加任务',exact:true}).click();await registration.getByLabel('任务名称 0',{exact:true}).fill('demo');await registration.getByRole('button',{name:'发布并运行',exact:true}).click();
 const runs=page.getByRole('region',{name:'集合运行向导'});await expect(runs).toBeVisible();await runs.getByLabel('批次名称',{exact:true}).fill('RoboTwin batch');await expect(runs.getByLabel('选择已保存集合')).toHaveValue('suite-1');
 await runs.getByRole('button',{name:'下一步',exact:true}).click();await runs.getByRole('button',{name:'下一步',exact:true}).click();await runs.getByLabel('推理服务地址',{exact:true}).fill('ws://inference:8000');await runs.getByRole('button',{name:'下一步',exact:true}).click();await runs.getByLabel('客户端资源池',{exact:true}).selectOption('pool-1');await runs.getByLabel('最大并发任务数').fill('2');await runs.getByRole('button',{name:'检查并确认',exact:true}).click();await runs.getByRole('button',{name:'提交评测批次',exact:true}).click();
 expect(submitted()).toMatchObject({suite_id:'suite-1',cases:[],max_concurrent:2,client:{template_id:'client-1',endpoints:[{url:'ws://inference:8000',mode:'shared'}]}});
 await page.getByRole('button',{name:'评测运行与结果',exact:true}).click();await expect(page.getByRole('row',{name:'批次 RoboTwin batch',exact:true})).toBeVisible();await expect(runs).toBeHidden();
});