import {test,expect} from '@playwright/test';

test('save reusable suite and run with concurrency; view partial episode results',async({page,request})=>{
  test.setTimeout(90000);
  const runTitle='browser-run-'+Date.now();
  const credentials={username:process.env.ADMIN_USERNAME!,password:process.env.ADMIN_PASSWORD!};
  const login=await request.post('/api/v1/auth/login',{data:credentials});
  const token=(await login.json()).access_token;
  const template=await request.post('/api/v1/admin/client-templates',{headers:{Authorization:'Bearer '+token},data:{name:'browser-sdk-'+Date.now(),version:'1',image:'mini-eval-cs-test:1',command:['python','-m','runner.sdk','runner.sdk_demo:DemoClient'],episode_reporting:true}});
  expect(template.status()).toBe(201);
  const t=await template.json();
  await page.goto('/');
  await page.getByLabel('用户名',{exact:true}).fill(credentials.username);
  await page.getByLabel('密码',{exact:true}).fill(credentials.password);
  await page.getByRole('button',{name:'登录',exact:true}).click();
  await page.getByRole('button',{name:'CS 评测批次',exact:true}).click();
  await page.getByRole('tab',{name:'评测集合管理',exact:true}).click();
  await page.getByRole('tabpanel',{name:'评测集合管理',exact:true}).getByRole('combobox',{name:'集合客户端模板',exact:true}).selectOption(t.id);
  await page.getByLabel('集合名称',{exact:true}).fill('browser-suite-'+Date.now());
  for(let i=0;i<2;i++){
    await page.getByRole('tabpanel',{name:'评测集合管理',exact:true}).getByRole('button',{name:'添加任务',exact:true}).click();
    await page.getByLabel('任务名称 '+i,{exact:true}).fill('demo');
    await page.getByLabel('任务回合 '+i,{exact:true}).fill('2');
  }
  await page.getByRole('button',{name:'发布并运行',exact:true}).click();
  await expect(page.getByLabel('选择已保存集合')).not.toHaveValue('');
  await page.getByLabel('批次名称',{exact:true}).fill(runTitle);
  await page.getByRole('button',{name:'下一步',exact:true}).click();
  await page.getByLabel('推理服务地址',{exact:true}).fill(process.env.CS_TEST_ENDPOINT||'http://192.168.137.71:19090/infer');
  await page.getByRole('button',{name:'下一步',exact:true}).click();
  await page.getByRole('combobox',{name:'客户端资源池',exact:true}).selectOption({label:process.env.CS_POOL_NAME||'mock'});
  await page.getByLabel('最大并发任务数').fill('2');
  await page.getByRole('button',{name:'检查并确认',exact:true}).click();
  const submitted=page.waitForResponse(r=>r.url().endsWith('/api/v1/batches')&&r.request().method()==='POST');
  await page.getByRole('button',{name:/^(提交评测批次|提交排队)$/}).click();
  const batch=await (await submitted).json();
  expect(batch.total).toBe(2);
  await expect.poll(async()=>{
    const response=await request.get('/api/v1/batches/'+batch.id,{headers:{Authorization:'Bearer '+token}});
    return (await response.json()).cases.map((c:any)=>({status:c.status,completed:c.progress.completed}));
  },{timeout:60000}).toEqual([{status:'SUCCEEDED',completed:2},{status:'SUCCEEDED',completed:2}]);
  await expect(page.getByRole('heading',{name:new RegExp(runTitle)})).toBeVisible();
  await page.getByRole('heading',{name:new RegExp(runTitle)}).last().locator('..').getByRole('button',{name:'回合结果',exact:true}).first().click();
  await expect(page.getByRole('heading',{name:'回合进度与部分结果'})).toBeVisible();
  await page.screenshot({path:'test-results/cs-framework.png',fullPage:true});
});
