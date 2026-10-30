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
    else if(path==='pools') body=[{id:'pool-1',name:'gpu-pool'}];
    else if(path==='suites') {
      if(route.request().method()==='POST') {body={id:'suite-1',...route.request().postDataJSON()};suites.push(body);}
      else body=suites;
    } else if(path==='batches') {
      if(route.request().method()==='POST') {
        submitted=route.request().postDataJSON();
        body={id:'batch-1',title:submitted.title,status:'PENDING',total:1,succeeded:0,finished:0,total_episodes:3,cases:[]};batches.push(body);
      } else body=batches;
    }
    await route.fulfill({json:body});
  });
  await page.goto('/');
  await page.getByRole('button',{name:'CS 评测批次',exact:true}).click();
  return ()=>submitted;
}

test('suite registration hands off to run; tabs preserve drafts and send selected suite',async({page})=>{
  const submitted=await openWorkbench(page);
  const runs=page.getByRole('tabpanel',{name:'评测集合运行',exact:true});
  const registration=page.getByRole('tabpanel',{name:'评测集合注册',exact:true});
  await expect(runs).toBeVisible();
  await runs.getByLabel('批次名称',{exact:true}).fill('RoboTwin batch');
  await page.getByRole('tab',{name:'评测集合注册',exact:true}).click();
  await expect(runs).toBeHidden();
  await registration.getByLabel('集合名称',{exact:true}).fill('RoboTwin full');
  await registration.getByLabel('客户端模板',{exact:true}).selectOption('client-1');
  await page.getByRole('tab',{name:'客户端注册',exact:true}).click();
  await expect(page.getByRole('button',{name:'登记客户端模板',exact:true})).toBeVisible();
  await page.getByRole('tab',{name:'评测集合注册',exact:true}).click();
  await expect(registration.getByLabel('集合名称',{exact:true})).toHaveValue('RoboTwin full');
  await registration.getByRole('button',{name:'保存集合版本',exact:true}).click();
  await expect(runs).toBeVisible();
  await expect(runs.getByLabel('批次名称',{exact:true})).toHaveValue('RoboTwin batch');
  await expect(runs.getByLabel('选择已保存集合')).toHaveValue('suite-1');
  await expect(runs.getByLabel('第 1 项任务名',{exact:true})).toBeDisabled();
  await runs.getByLabel('推理服务地址',{exact:true}).fill('ws://inference:8000');
  await runs.getByLabel('客户端资源池',{exact:true}).selectOption('pool-1');
  await runs.getByLabel('最大并发任务数').fill('2');
  await runs.getByRole('button',{name:'提交评测批次',exact:true}).click();
  await expect(runs.getByRole('heading',{name:/RoboTwin batch/})).toBeVisible();
  expect(submitted()).toMatchObject({suite_id:'suite-1',cases:[],max_concurrent:2,client:{template_id:'client-1',endpoint:'ws://inference:8000'}});
  await page.screenshot({path:'test-results/cs-tabs-runs.png',fullPage:true});
});

test('member can register source without admin controls; keyboard switches tabs',async({page})=>{
  await openWorkbench(page,'member');
  const runTab=page.getByRole('tab',{name:'评测集合运行',exact:true});
  await runTab.focus();await page.keyboard.press('ArrowRight');
  const clientTab=page.getByRole('tab',{name:'客户端注册',exact:true});
  await expect(clientTab).toBeFocused();await expect(clientTab).toHaveAttribute('aria-selected','true');
  await expect(page.getByRole('heading',{name:'提交仿真客户端代码'})).toBeVisible();
  await expect(page.getByRole('button',{name:'登记客户端模板',exact:true})).toHaveCount(0);
  await expect(page.getByRole('heading',{name:'可用客户端模板'})).toBeVisible();
  await page.keyboard.press('Home');
  await expect(page.getByRole('tab',{name:'评测集合注册',exact:true})).toBeFocused();
  await expect(page.getByRole('tabpanel',{name:'评测集合注册',exact:true})).toBeVisible();
});
